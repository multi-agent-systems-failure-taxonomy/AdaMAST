"""Program identity and manifest state anchored by mandatory trace_output."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .fsio import read_text_retry, replace_retry
from .repository import discover_repo
from .traces import TraceStore
from .worker_state import (
    DEFAULT_WORKER_STALE_AFTER_SECONDS,
    GENERATION_WORKER_STATE,
    REFINEMENT_WORKER_STATE,
    worker_state_is_stale,
)

MANIFEST_NAME = ".adamast-program.json"

# User-level interactive installs run taxonomy learning through the signed-in
# host CLI, so their configured model is this placeholder rather than a real
# learning model. It adopts whatever model a program already records instead
# of conflicting with it: package-default renames must not brick every
# project state written by an earlier release.
INTERACTIVE_SESSION_MODEL = "interactive-session"
DEFAULT_SESSION_STALE_AFTER_SECONDS = 6 * 60 * 60


class ProgramConflict(ValueError):
    """Raised when a run conflicts with the program's bound taxonomy."""


def _prune_stale_sessions(
    manifest: dict[str, Any],
    *,
    now: float,
    stale_after_seconds: float,
) -> list[str]:
    """Prune expired leases while giving legacy untimed records one grace lease."""
    active = manifest.setdefault("active_sessions", [])
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    for item in active:
        if not isinstance(item, dict):
            continue
        heartbeat = item.get("heartbeat_at_unix")
        if not isinstance(heartbeat, int | float):
            item.setdefault("started_at_unix", now)
            item["heartbeat_at_unix"] = now
            kept.append(item)
            continue
        if now - float(heartbeat) > stale_after_seconds:
            removed.append(str(item.get("session_id") or "unknown"))
            continue
        kept.append(item)
    manifest["active_sessions"] = kept
    return removed


class ProgramWorkspace:
    """Stable program identity derived from a user-selected trace directory."""

    def __init__(
        self,
        trace_output: Path | str,
        *,
        repo: str | None = None,
        repo_path: Path | str | None = None,
    ) -> None:
        if trace_output is None or not str(trace_output).strip():
            raise ValueError("trace_output is required for every AdaMAST run")
        self.root = Path(trace_output).expanduser().resolve()
        self.manifest_path = self.root / MANIFEST_NAME
        self.pending = TraceStore(self.root / "pending")
        self.root.mkdir(parents=True, exist_ok=True)
        discovered_repo = discover_repo(repo, repo_path)
        with self.locked_manifest() as manifest:
            if not manifest:
                manifest.update(self._new_manifest(discovered_repo))
            elif not manifest.get("repo"):
                manifest["repo"] = discovered_repo
            elif repo is not None and manifest["repo"] != discovered_repo:
                raise ProgramConflict(
                    f"program already records repo {manifest['repo']!r}, not "
                    f"{discovered_repo!r}"
                )

    @staticmethod
    def _new_manifest(repo: str = "") -> dict[str, Any]:
        return {
            "version": 1,
            "program_id": f"program-{uuid.uuid4()}",
            "repo": repo,
            "adamast_model": None,
            "taxonomy_id": None,
            "active_sessions": [],
            "generation": {
                "state": "idle",
                "last_error": None,
                # None means "no rejection yet": the configured
                # generation_threshold decides when the first generation
                # fires. A hardcoded count here would override the config.
                "retry_after_count": None,
                "last_check_snapshot_count": 0,
            },
            "refinement": {
                "rounds_completed": 0,
                "traces_since_refinement": 0,
                "trace_refs": [],
                "state": "idle",
                "last_error": None,
            },
            "usage": {
                "totals": {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                },
                "events": [],
            },
        }

    def load(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        text = read_text_retry(self.manifest_path)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # A corrupt manifest must not brick every command with a raw
            # traceback. Quarantine it for manual inspection and fail with an
            # actionable error; the next run starts a fresh manifest (the
            # taxonomy store and trace collections are untouched).
            quarantine = self.manifest_path.with_name(
                f"{MANIFEST_NAME}.corrupt-{int(time.time())}"
            )
            try:
                os.replace(self.manifest_path, quarantine)
            except OSError:
                quarantine = self.manifest_path
            raise ProgramConflict(
                f"program manifest at {self.manifest_path} was corrupt "
                f"({exc}); it was moved to {quarantine}. The next run will "
                "create a fresh manifest; restore the quarantined file "
                "manually if this program's taxonomy binding matters."
            ) from exc

    @property
    def program_id(self) -> str:
        return str(self.load()["program_id"])

    @property
    def repo(self) -> str:
        return str(self.load().get("repo", ""))

    @property
    def branch_id(self) -> str | None:
        """Return the conversation-owned branch id for schema-v2 programs."""
        branch = self.load().get("branch")
        if not isinstance(branch, dict):
            return None
        value = str(branch.get("branch_id") or "").strip()
        return value or None

    def bind_conversation_branch(
        self,
        branch_id: str,
        *,
        conversation_id: str,
        host: str,
    ) -> None:
        """Bind a new interactive program to exactly one conversation branch.

        Legacy programs intentionally have no ``branch`` block and retain their
        historical storage layout. New interactive routes call this once before
        selector state is created, making trace and learning ownership explicit.
        """
        branch_id = str(branch_id or "").strip()
        conversation_id = str(conversation_id or "").strip()
        host = str(host or "").strip()
        if not branch_id or not conversation_id or not host:
            raise ValueError("branch_id, conversation_id, and host are required")
        with self.locked_manifest() as manifest:
            existing = manifest.get("branch")
            expected = {
                "branch_id": branch_id,
                "conversation_id": conversation_id,
                "host": host,
            }
            if isinstance(existing, dict):
                for key, value in expected.items():
                    recorded = str(existing.get(key) or "").strip()
                    if recorded and recorded != value:
                        raise ProgramConflict(
                            f"program branch {key} is {recorded!r}, not {value!r}"
                        )
                existing.update(expected)
                existing.setdefault("seed_taxonomy_id", None)
                existing["head_taxonomy_id"] = manifest.get("taxonomy_id")
            else:
                manifest["branch"] = {
                    **expected,
                    "seed_taxonomy_id": manifest.get("taxonomy_id"),
                    "head_taxonomy_id": manifest.get("taxonomy_id"),
                }

    def scoped_trace_root(self, trace_root: Path | str) -> Path:
        """Return a branch-owned trace root, preserving the legacy layout."""
        root = Path(trace_root).expanduser().resolve()
        branch_id = self.branch_id
        if not branch_id:
            return root
        if len(root.parts) >= 2 and root.parts[-2:] == ("branches", branch_id):
            return root
        return root / "branches" / branch_id

    def assert_trace_owner(self, trace: Any) -> None:
        """Reject cross-conversation evidence in a conversation-owned branch."""
        branch = self.load().get("branch")
        if not isinstance(branch, dict):
            return
        expected = str(branch.get("conversation_id") or "").strip()
        if not expected:
            return
        metadata = getattr(trace, "metadata", None)
        if not isinstance(metadata, dict):
            raise ProgramConflict("branch trace has no ownership metadata")
        actual = str(metadata.get("conversation_id") or "").strip()
        if actual != expected:
            raise ProgramConflict(
                f"trace belongs to conversation {actual or 'unknown'!r}, not "
                f"branch conversation {expected!r}"
            )

    def register_session(self, session_id: str, taxonomy_id: str) -> None:
        now = time.time()
        with self.locked_manifest() as manifest:
            sessions = manifest.setdefault("active_sessions", [])
            sessions.append(
                {
                    "session_id": session_id,
                    "taxonomy_id": taxonomy_id,
                    "started_at_unix": now,
                    "heartbeat_at_unix": now,
                }
            )

    def begin_session(
        self,
        session_id: str,
        requested_taxonomy_id: str | None,
        adamast_model: str | None = None,
    ) -> str:
        """Choose the program taxonomy and register a running task atomically."""
        with self.locked_manifest() as manifest:
            current = manifest.get("taxonomy_id")
            configured_model = manifest.get("adamast_model")
            if (
                configured_model
                and adamast_model
                and configured_model != adamast_model
                and adamast_model != INTERACTIVE_SESSION_MODEL
            ):
                raise ProgramConflict(
                    f"program already uses AdaMAST model {configured_model!r}, not "
                    f"{adamast_model!r}"
                )
            if adamast_model and not configured_model:
                manifest["adamast_model"] = adamast_model
            if current and requested_taxonomy_id and current != requested_taxonomy_id:
                raise ProgramConflict(
                    f"program already uses taxonomy {current!r}, not "
                    f"{requested_taxonomy_id!r}"
                )
            selected = current or requested_taxonomy_id or "mast"
            branch = manifest.get("branch")
            if isinstance(branch, dict):
                if branch.get("seed_taxonomy_id") is None:
                    branch["seed_taxonomy_id"] = selected
                branch["head_taxonomy_id"] = selected
            if requested_taxonomy_id and not current:
                manifest["taxonomy_id"] = requested_taxonomy_id
                manifest["generation"] = {
                    "state": "not_needed",
                    "last_error": None,
                }
            now = time.time()
            manifest.setdefault("active_sessions", []).append(
                {
                    "session_id": session_id,
                    "taxonomy_id": selected,
                    "started_at_unix": now,
                    "heartbeat_at_unix": now,
                }
            )
            return str(selected)

    def heartbeat_session(
        self,
        session_id: str,
        *,
        now: float | None = None,
    ) -> bool:
        """Refresh a live session lease and migrate legacy untimed records."""
        current = time.time() if now is None else float(now)
        found = False
        with self.locked_manifest() as manifest:
            for item in manifest.get("active_sessions", []):
                if not isinstance(item, dict) or item.get("session_id") != session_id:
                    continue
                item.setdefault("started_at_unix", current)
                item["heartbeat_at_unix"] = current
                found = True
        return found

    def reconcile_stale_sessions(
        self,
        *,
        now: float | None = None,
        stale_after_seconds: float = DEFAULT_SESSION_STALE_AFTER_SECONDS,
    ) -> list[str]:
        """Expire abandoned leases; untimed legacy records receive one grace lease."""
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        current = time.time() if now is None else float(now)
        with self.locked_manifest() as manifest:
            return _prune_stale_sessions(
                manifest,
                now=current,
                stale_after_seconds=stale_after_seconds,
            )

    def follow_taxonomy_successor(self, taxonomy_id: str) -> None:
        """Advance taxonomy identity without touching program-local progress."""
        with self.locked_manifest() as manifest:
            manifest["taxonomy_id"] = taxonomy_id
            if isinstance(manifest.get("branch"), dict):
                manifest["branch"]["head_taxonomy_id"] = taxonomy_id

    def add_refinement_traces(
        self,
        taxonomy_id: str,
        filenames: list[str],
    ) -> int:
        with self.locked_manifest() as manifest:
            refinement = manifest.setdefault(
                "refinement",
                self._new_manifest()["refinement"],
            )
            refs = refinement.setdefault("trace_refs", [])
            existing = {
                (str(item.get("taxonomy_id")), str(item.get("filename")))
                for item in refs
                if isinstance(item, dict)
            }
            additions = [
                {"taxonomy_id": taxonomy_id, "filename": name}
                for name in filenames
                if (taxonomy_id, name) not in existing
            ]
            refs.extend(additions)
            refinement["traces_since_refinement"] = int(
                refinement.get("traces_since_refinement", 0)
            ) + len(additions)
            return int(refinement["traces_since_refinement"])

    def refinement_state(self) -> dict[str, Any]:
        return dict(
            self.load().get("refinement")
            or self._new_manifest()["refinement"]
        )

    def try_begin_refinement(
        self,
        threshold: int,
        *,
        worker_kind: str = "inline",
        worker_stale_after_seconds: float = DEFAULT_WORKER_STALE_AFTER_SECONDS,
    ) -> bool:
        with self.locked_manifest() as manifest:
            refinement = manifest.setdefault(
                "refinement",
                self._new_manifest()["refinement"],
            )
            if int(refinement.get("traces_since_refinement", 0)) < threshold:
                return False
            if refinement.get("state") == "running":
                existing_kind = refinement.get("worker_kind")
                if existing_kind in (None, "background") and self._worker_is_stale(
                    refinement,
                    REFINEMENT_WORKER_STATE,
                    worker_stale_after_seconds,
                    legacy_without_timestamp_is_stale=existing_kind is None,
                ):
                    refinement["state"] = "failed"
                    refinement["last_error"] = (
                        "previous background refinement worker became stale"
                    )
                else:
                    return False
            refinement["state"] = "running"
            refinement["last_error"] = None
            refinement["worker_kind"] = worker_kind
            refinement["worker_started_unix"] = time.time()
            return True

    def mark_refinement(self, state: str, error: str | None = None) -> None:
        with self.locked_manifest() as manifest:
            refinement = manifest.setdefault(
                "refinement",
                self._new_manifest()["refinement"],
            )
            refinement["state"] = state
            refinement["last_error"] = error
            if state != "running":
                refinement.pop("worker_kind", None)
                refinement.pop("worker_started_unix", None)

    def complete_refinement(self, taxonomy_id: str) -> None:
        with self.locked_manifest() as manifest:
            refinement = manifest.setdefault(
                "refinement",
                self._new_manifest()["refinement"],
            )
            manifest["taxonomy_id"] = taxonomy_id
            if isinstance(manifest.get("branch"), dict):
                manifest["branch"]["head_taxonomy_id"] = taxonomy_id
            refinement["rounds_completed"] = int(
                refinement.get("rounds_completed", 0)
            ) + 1
            refinement["traces_since_refinement"] = 0
            refinement["trace_refs"] = []
            refinement["state"] = "complete"
            refinement["last_error"] = None
            refinement.pop("worker_kind", None)
            refinement.pop("worker_started_unix", None)

    def record_usage_event(
        self,
        *,
        stage: str,
        model: str | None = None,
        provider: str | None = None,
        usage_available: bool = False,
        calls: int = 1,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append an honest learning-call usage event to the program manifest.

        Many supported transports do not expose token or cost metadata. In
        those cases AdaMAST records the call and marks usage unavailable instead
        of inventing estimates.
        """
        event = {
            "timestamp_unix": time.time(),
            "stage": stage,
            "model": model,
            "provider": provider,
            "usage_available": bool(usage_available),
            "calls": int(calls),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "details": details or {},
        }
        with self.locked_manifest() as manifest:
            usage = manifest.setdefault("usage", self._new_manifest()["usage"])
            totals = usage.setdefault("totals", {})
            totals["calls"] = int(totals.get("calls", 0)) + event["calls"]
            for key, value in (
                ("input_tokens", input_tokens),
                ("output_tokens", output_tokens),
            ):
                if isinstance(value, int):
                    totals[key] = int(totals.get(key, 0)) + value
            if isinstance(cost_usd, int | float):
                totals["cost_usd"] = float(totals.get("cost_usd", 0.0)) + float(
                    cost_usd
                )
            totals.setdefault("input_tokens", 0)
            totals.setdefault("output_tokens", 0)
            totals.setdefault("cost_usd", 0.0)
            events = usage.setdefault("events", [])
            events.append(event)
            del events[:-200]

    def finish_session(self, session_id: str) -> None:
        with self.locked_manifest() as manifest:
            manifest["active_sessions"] = [
                item
                for item in manifest.get("active_sessions", [])
                if item.get("session_id") != session_id
            ]

    def bind_inherited_taxonomy(self, taxonomy_id: str) -> None:
        with self.locked_manifest() as manifest:
            current = manifest.get("taxonomy_id")
            if current and current != taxonomy_id:
                raise ProgramConflict(
                    f"program already uses taxonomy {current!r}, not {taxonomy_id!r}"
                )
            manifest["taxonomy_id"] = taxonomy_id
            if isinstance(manifest.get("branch"), dict):
                branch = manifest["branch"]
                if branch.get("seed_taxonomy_id") is None:
                    branch["seed_taxonomy_id"] = taxonomy_id
                branch["head_taxonomy_id"] = taxonomy_id
            manifest["generation"] = {
                "state": "not_needed",
                "last_error": None,
            }

    def generation_state(self) -> str:
        return str(self.load().get("generation", {}).get("state", "idle"))

    def mark_generation(self, state: str, error: str | None = None) -> None:
        with self.locked_manifest() as manifest:
            generation = manifest.setdefault("generation", {})
            generation["state"] = state
            generation["last_error"] = error
            if state != "running":
                generation.pop("worker_kind", None)
                generation.pop("worker_started_unix", None)

    def mark_generation_rejected(
        self,
        snapshot_count: int,
        threshold: int,
        error: str | None = None,
    ) -> None:
        with self.locked_manifest() as manifest:
            generation = manifest.setdefault("generation", {})
            generation["state"] = "rejected"
            generation["last_error"] = error
            generation["last_check_snapshot_count"] = snapshot_count
            generation["retry_after_count"] = snapshot_count + threshold
            generation.pop("worker_kind", None)
            generation.pop("worker_started_unix", None)

    def try_begin_generation(
        self,
        *,
        worker_kind: str = "inline",
        worker_stale_after_seconds: float = DEFAULT_WORKER_STALE_AFTER_SECONDS,
    ) -> bool:
        with self.locked_manifest() as manifest:
            if manifest.get("taxonomy_id"):
                return False
            generation = manifest.setdefault("generation", {})
            if generation.get("state") == "running":
                existing_kind = generation.get("worker_kind")
                if existing_kind in (None, "background") and self._worker_is_stale(
                    generation,
                    GENERATION_WORKER_STATE,
                    worker_stale_after_seconds,
                    legacy_without_timestamp_is_stale=existing_kind is None,
                ):
                    generation["state"] = "failed"
                    generation["last_error"] = (
                        "previous background generation worker became stale"
                    )
                else:
                    return False
            generation["state"] = "running"
            generation["last_error"] = None
            generation["worker_kind"] = worker_kind
            generation["worker_started_unix"] = time.time()
            return True

    def generation_retry_after(self, default: int) -> int:
        value = self.load().get("generation", {}).get("retry_after_count")
        return int(value) if value is not None else int(default)

    def activate_if_idle(self, taxonomy_id: str) -> bool:
        """Atomically activate only when no task is running."""
        with self.locked_manifest() as manifest:
            _prune_stale_sessions(
                manifest,
                now=time.time(),
                stale_after_seconds=DEFAULT_SESSION_STALE_AFTER_SECONDS,
            )
            if manifest.get("active_sessions"):
                return False
            if manifest.get("taxonomy_id"):
                return manifest["taxonomy_id"] == taxonomy_id
            manifest["taxonomy_id"] = taxonomy_id
            if isinstance(manifest.get("branch"), dict):
                manifest["branch"]["head_taxonomy_id"] = taxonomy_id
            manifest["generation"] = {"state": "complete", "last_error": None}
            return True

    def _worker_is_stale(
        self,
        job: dict[str, Any],
        filename: str,
        stale_after_seconds: float,
        *,
        legacy_without_timestamp_is_stale: bool,
    ) -> bool:
        worker_path = self.root / filename
        if worker_path.exists():
            return worker_state_is_stale(
                worker_path,
                stale_after_seconds=stale_after_seconds,
                missing_is_stale=False,
            )
        started = job.get("worker_started_unix")
        if isinstance(started, int | float):
            return time.time() - float(started) > stale_after_seconds
        return legacy_without_timestamp_is_stale

    @contextmanager
    def locked_manifest(self, *, timeout: float = 5.0, stale_after: float = 60.0):
        lock = self.root / ".manifest.lock"
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
                    raise TimeoutError(f"timed out waiting for program lock {lock}")
                time.sleep(0.05)
        try:
            # load() may raise (e.g. corrupt-manifest quarantine); it must run
            # inside the try so the lock is always released.
            manifest = self.load()
            before = json.dumps(manifest, indent=2, ensure_ascii=False)
            yield manifest
            rendered = json.dumps(manifest, indent=2, ensure_ascii=False)
            # Read-only critical sections (activation polls, cadence checks)
            # must not churn the file: every replace is a chance to collide
            # with an unlocked reader on Windows.
            if rendered != before or not self.manifest_path.exists():
                temporary = self.root / f".{MANIFEST_NAME}.{os.getpid()}.tmp"
                temporary.write_text(rendered + "\n", encoding="utf-8")
                replace_retry(temporary, self.manifest_path)
        finally:
            try:
                lock.rmdir()
            except FileNotFoundError:
                pass
