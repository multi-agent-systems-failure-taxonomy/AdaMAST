"""Crash-safe trace files in the exact AdaMAST generation-input shape.

Each trace is an independent JSON record with exactly:

    problem_id, task, raw_trajectory, metadata

Program warm-up traces first land in ``<trace_output>/pending``. Approved or
inherited taxonomy traces live in ``<trace_root>/<taxonomy_id>``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .fsio import read_text_retry, replace_retry, write_text_atomic_retry

DEFAULT_ADAMAST_HOME = Path(
    os.environ.get("ADAMAST_HOME", Path.home() / ".adamast")
).expanduser()
DEFAULT_TRACE_ROOT = Path(
    os.environ.get("ADAMAST_TRACE_ROOT", DEFAULT_ADAMAST_HOME / "traces")
).expanduser()
TRACE_FIELDS = ("problem_id", "task", "raw_trajectory", "metadata")


class TraceReadError(RuntimeError):
    """A stored trace could not be read or validated without evidence loss."""

    def __init__(self, path: Path, reason: Exception) -> None:
        self.path = path
        super().__init__(f"invalid or unreadable trace {path}: {reason}")


@dataclass(frozen=True)
class GenerationTrace:
    """One canonical trace consumable by AdaMAST taxonomy generation."""

    problem_id: str
    task: str
    raw_trajectory: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.problem_id, str) or not self.problem_id.strip():
            raise ValueError("problem_id must be a non-empty string")
        if not isinstance(self.task, str):
            raise TypeError("task must be a string")
        if not isinstance(self.raw_trajectory, str) or not self.raw_trajectory.strip():
            raise ValueError("raw_trajectory must be a non-empty string")
        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be an object")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> "GenerationTrace":
        if set(record) != set(TRACE_FIELDS):
            raise ValueError(
                f"trace fields must be exactly {TRACE_FIELDS}; got {tuple(record)}"
            )
        return cls(**record)


@dataclass(frozen=True)
class RetentionPolicy:
    """Warning thresholds; records never expire automatically."""

    max_total_records: int = 10_000
    max_age_days: int = 90

    def __post_init__(self) -> None:
        if self.max_total_records <= 0:
            raise ValueError("max_total_records must be positive")
        if self.max_age_days <= 0:
            raise ValueError("max_age_days must be positive")


@dataclass(frozen=True)
class RetentionReport:
    total_records: int
    file_count: int
    oldest_file_age_days: float
    record_limit_exceeded: bool
    age_limit_exceeded: bool
    automatic_deletion: bool = False

    @property
    def needs_attention(self) -> bool:
        return self.record_limit_exceeded or self.age_limit_exceeded


class TraceStore:
    """One-JSON-file-per-trace store with verified integration."""

    def __init__(
        self,
        root: Path | str,
        *,
        policy: RetentionPolicy | None = None,
    ) -> None:
        self.root = Path(root)
        self.policy = policy or RetentionPolicy()

    def append_many(self, traces: Iterable[GenerationTrace]) -> int:
        return len(self.append_many_with_names(traces))

    def append_many_with_names(
        self,
        traces: Iterable[GenerationTrace],
    ) -> list[str]:
        pending = list(traces)
        if not pending:
            return []
        self.root.mkdir(parents=True, exist_ok=True)
        names: list[str] = []
        with self._write_lock():
            for trace in pending:
                name = f"trace-{uuid.uuid4().hex}.json"
                self._write_atomic(self.root / name, trace.to_dict())
                names.append(name)
        return names

    def iter_traces(self) -> Iterator[GenerationTrace]:
        for path in self.trace_files():
            try:
                # A transiently locked file must not be silently dropped: a
                # short snapshot changes the evidence hash and strands the
                # learning job with a collision.
                record = json.loads(read_text_retry(path))
                yield GenerationTrace.from_dict(record)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise TraceReadError(path, exc) from exc

    def trace_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.glob("trace-*.json"))

    def count(self) -> int:
        return sum(1 for _ in self.iter_traces())

    def integrate_into(self, destination: "TraceStore") -> int:
        """Copy, verify, then remove source traces.

        A source is never deleted until the destination bytes match. Existing
        identical destination files make retries idempotent.
        """
        files = self.trace_files()
        if not files:
            return 0
        destination.root.mkdir(parents=True, exist_ok=True)
        integrated = 0
        with self._write_lock(), destination._write_lock():
            for source in files:
                payload = source.read_bytes()
                target = destination.root / source.name
                if target.exists() and target.read_bytes() != payload:
                    digest = hashlib.sha256(payload).hexdigest()[:12]
                    target = destination.root / f"{source.stem}-{digest}.json"
                if not target.exists():
                    temporary = destination.root / f".{target.name}.{os.getpid()}.tmp"
                    temporary.write_bytes(payload)
                    replace_retry(temporary, target)
                if target.read_bytes() != payload:
                    raise OSError(f"trace verification failed for {target}")
                source.unlink()
                integrated += 1
        return integrated

    def retention_report(self, *, now: float | None = None) -> RetentionReport:
        files = self.trace_files()
        total = self.count()
        current = time.time() if now is None else now
        ages = [
            max(0.0, current - path.stat().st_mtime) / 86_400
            for path in files
        ]
        oldest = max(ages, default=0.0)
        return RetentionReport(
            total_records=total,
            file_count=len(files),
            oldest_file_age_days=oldest,
            record_limit_exceeded=total > self.policy.max_total_records,
            age_limit_exceeded=oldest > self.policy.max_age_days,
        )

    @staticmethod
    def _write_atomic(path: Path, record: dict[str, Any]) -> None:
        write_text_atomic_retry(
            path,
            json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        )

    @contextmanager
    def _write_lock(self, *, timeout: float = 5.0, stale_after: float = 60.0):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / ".write.lock"
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > stale_after:
                        lock.rmdir()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for trace-store lock {lock}")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                lock.rmdir()
            except FileNotFoundError:
                pass
