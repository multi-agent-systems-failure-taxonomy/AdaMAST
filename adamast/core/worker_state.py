"""Durable heartbeat files for detached AdaMAST background workers."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fsio import read_text_retry, write_text_atomic_retry

GENERATION_WORKER_STATE = "generation_worker.json"
REFINEMENT_WORKER_STATE = "refinement_worker.json"
DEFAULT_WORKER_STALE_AFTER_SECONDS = 300.0


def write_worker_state(
    path: Path | str,
    kind: str,
    *,
    pid: int | None = None,
    now: float | None = None,
    state: str = "running",
) -> None:
    """Atomically write a background-worker heartbeat record."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.time() if now is None else float(now)
    payload: dict[str, Any] = {
        "kind": kind,
        "pid": os.getpid() if pid is None else int(pid),
        "state": state,
        "started_at": _format_time(timestamp),
        "heartbeat_at": _format_time(timestamp),
        "heartbeat_unix": timestamp,
    }
    write_text_atomic_retry(
        target,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def touch_worker_heartbeat(path: Path | str, kind: str) -> None:
    """Refresh the heartbeat timestamp while preserving the start metadata."""
    target = Path(path)
    now = time.time()
    payload = _read_payload(target) or {
        "kind": kind,
        "pid": os.getpid(),
        "state": "running",
        "started_at": _format_time(now),
    }
    payload.update(
        {
            "kind": kind,
            "pid": int(payload.get("pid") or os.getpid()),
            "state": "running",
            "heartbeat_at": _format_time(now),
            "heartbeat_unix": now,
        }
    )
    write_text_atomic_retry(
        target,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def worker_state_is_stale(
    path: Path | str,
    *,
    stale_after_seconds: float = DEFAULT_WORKER_STALE_AFTER_SECONDS,
    missing_is_stale: bool = True,
    now: float | None = None,
) -> bool:
    """Return true when a worker heartbeat is old, missing, or unreadable."""
    target = Path(path)
    if not target.exists():
        return missing_is_stale
    payload = _read_payload(target)
    if not payload:
        return True
    heartbeat = _heartbeat_unix(payload)
    if heartbeat is None:
        return True
    current = time.time() if now is None else float(now)
    return current - heartbeat > stale_after_seconds


def clear_worker_state(path: Path | str) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


class WorkerHeartbeat(AbstractContextManager["WorkerHeartbeat"]):
    """Context manager that refreshes a worker-state file in a daemon thread."""

    def __init__(
        self,
        path: Path | str,
        kind: str,
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        self.path = Path(path)
        self.kind = kind
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "WorkerHeartbeat":
        write_worker_state(self.path, self.kind)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 1.0)
        clear_worker_state(self.path)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            touch_worker_heartbeat(self.path, self.kind)


def _read_payload(path: Path) -> dict[str, Any] | None:
    # A transient sharing violation must not read as "stale worker": that
    # verdict lets a supervisor requeue a job out from under a healthy worker.
    try:
        value = json.loads(read_text_retry(path))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _heartbeat_unix(payload: dict[str, Any]) -> float | None:
    value = payload.get("heartbeat_unix")
    if isinstance(value, int | float):
        return float(value)
    return None


def _format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
