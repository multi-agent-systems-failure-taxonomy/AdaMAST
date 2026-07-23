"""Shared runtime-evidence recording for AdaMAST integrations."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .fsio import read_text_retry, write_text_atomic_retry
from .reflection import ReflectionResult

EVIDENCE_FILE = ".adamast-runtime-evidence.json"


def record_reflection(
    trace_output: Path,
    state: dict[str, Any],
    reflection: ReflectionResult,
    *,
    gate: str,
    task_id: str,
    agent_id: str | None = None,
    agent_type: str | None = None,
    turn_id: str | None = None,
    episode_sequence: int | None = None,
    task_group: str | None = None,
    gate_status: str | None = None,
    source: str = "reflection",
) -> str:
    """Atomically append fired-code evidence scoped to one taxonomy ID."""
    path = trace_output / EVIDENCE_FILE
    with _file_lock(path):
        try:
            data = json.loads(read_text_retry(path))
        except (OSError, json.JSONDecodeError):
            data = {"version": 1, "taxonomies": {}, "checkpoints": []}
        taxonomy_id = str(state["taxonomy_id"])
        taxonomy = data.setdefault("taxonomies", {}).setdefault(
            taxonomy_id, {"codes": {}}
        )
        timestamp = time.time()
        event_base = {
            "checkpoint_id": reflection.checkpoint_id,
            "gate": gate,
            "task_id": task_id,
            "session_id": state["session_id"],
            "conversation_id": state.get("conversation_id", state["session_id"]),
            "agent_id": agent_id,
            "agent_type": agent_type,
            "turn_id": turn_id,
            "episode_sequence": (
                episode_sequence
                if episode_sequence is not None
                else state.get("episode_sequence")
            ),
            "task_group": task_group or state.get("task_group"),
            "gate_status": gate_status,
            "source": source,
            "observe": reflection.observe,
            "correlate": reflection.correlate,
            "decide": reflection.decide,
            "timestamp": timestamp,
        }
        for assignment in reflection.assignments:
            code = taxonomy.setdefault("codes", {}).setdefault(
                assignment.code_id,
                {"fire_count": 0, "task_firings": {}, "events": []},
            )
            code["fire_count"] = int(code.get("fire_count", 0)) + 1
            firings = code.setdefault("task_firings", {})
            firings[task_id] = int(firings.get(task_id, 0)) + 1
            code.setdefault("events", []).append(
                {**event_base, "evidence": assignment.evidence}
            )
        data.setdefault("checkpoints", []).append(
            {
                **event_base,
                "taxonomy_id": taxonomy_id,
                "none_apply": reflection.none_apply,
                "considered_codes": list(reflection.considered_codes),
                "fired_codes": [
                    assignment.code_id
                    for assignment in reflection.assignments
                ],
            }
        )
        write_text_atomic_retry(
            path,
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        )
    return reflection.checkpoint_id


def annotate_checkpoints(
    trace_output: Path,
    checkpoint_ids: list[str] | tuple[str, ...] | set[str],
    *,
    turn_id: str | None,
    gate_status: str | None = None,
) -> int:
    """Attach host turn metadata to checkpoints recorded before Stop fires."""
    wanted = {str(value) for value in checkpoint_ids if str(value).strip()}
    if not wanted:
        return 0
    path = Path(trace_output) / EVIDENCE_FILE
    with _file_lock(path):
        try:
            data = json.loads(read_text_retry(path))
        except (OSError, json.JSONDecodeError):
            return 0
        updated: set[str] = set()
        for checkpoint in data.get("checkpoints", []):
            if not isinstance(checkpoint, dict):
                continue
            checkpoint_id = str(checkpoint.get("checkpoint_id") or "")
            if checkpoint_id not in wanted:
                continue
            checkpoint["turn_id"] = turn_id
            if gate_status is not None and checkpoint.get("gate") == "stop":
                checkpoint["gate_status"] = gate_status
            updated.add(checkpoint_id)
        for taxonomy in data.get("taxonomies", {}).values():
            if not isinstance(taxonomy, dict):
                continue
            for code in taxonomy.get("codes", {}).values():
                if not isinstance(code, dict):
                    continue
                for event in code.get("events", []):
                    if not isinstance(event, dict):
                        continue
                    checkpoint_id = str(event.get("checkpoint_id") or "")
                    if checkpoint_id not in wanted:
                        continue
                    event["turn_id"] = turn_id
                    if gate_status is not None and event.get("gate") == "stop":
                        event["gate_status"] = gate_status
        if updated:
            write_text_atomic_retry(
                path,
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            )
        return len(updated)


@contextmanager
def _file_lock(path: Path, *, timeout: float = 5.0, stale_after: float = 30.0):
    lock = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock.mkdir(parents=True)
            break
        except FileExistsError:
            # A writer killed mid-write leaves the lock directory behind
            # forever, silently disabling evidence recording for the whole
            # program. Break stale locks, mirroring locked_manifest().
            try:
                if time.time() - lock.stat().st_mtime > stale_after:
                    lock.rmdir()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except FileNotFoundError:
            pass
