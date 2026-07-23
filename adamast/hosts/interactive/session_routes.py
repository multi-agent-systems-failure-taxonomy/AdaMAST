"""Host-neutral conversation scope and fresh taxonomy branch routing."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adamast.core.fsio import read_text_retry, write_text_atomic_retry
from adamast.core.program import ProgramWorkspace
from adamast.core.project_scope import project_program_path, validate_scope_id


@dataclass(frozen=True)
class SessionRoute:
    task_group: str
    trace_output: Path
    branch_id: str | None = None


def event_session_id(event: dict[str, Any]) -> str:
    for key in ("session_id", "thread_id", "conversation_id"):
        value = event.get(key)
        if value:
            return str(value)
    return str(event.get("transcript_path") or "").strip()


def resolve_conversation_scope(
    routing_root: Path,
    event: dict[str, Any],
    *,
    default_trace_output: Path,
    default_task_group: str,
    scope_dir: str,
    state_dir: str,
    host_label: str,
    host: str | None = None,
    isolate_conversation: bool = False,
) -> SessionRoute | None:
    """Pin one host conversation to one program despite later cwd drift.

    Interactive hosts report their current working directory on every event.
    Resuming a conversation after ``cd``-ing (or from a different shell) must
    not turn that existing conversation into a new AdaMAST session.  Existing
    installations are migrated by locating an already selected state before
    the binding is written.
    """
    session_id = event_session_id(event)
    if not session_id:
        return None
    root = Path(routing_root).expanduser().resolve()
    path = _conversation_scope_path(root, session_id, scope_dir=scope_dir)
    resolved = _read_scope_record(path, root, host=host)
    if resolved:
        _bind_route_branch(resolved, event, host=host)
        return resolved

    migrated = _discover_selected_scope(
        root,
        session_id,
        state_dir=state_dir,
        default_task_group=default_task_group,
        host=host,
    )
    target = migrated
    if target is None and isolate_conversation:
        target = _conversation_branch_route(
            root,
            session_id=session_id,
            default_trace_output=default_trace_output,
            host=host,
        )
    if target is None:
        target = SessionRoute(
            task_group=validate_scope_id(
                default_task_group,
                label="task_group",
            ),
            trace_output=Path(default_trace_output).expanduser().resolve(),
        )
    if not _is_within(target.trace_output, root):
        raise ValueError(
            f"{host_label} conversation scope must remain inside the AdaMAST "
            f"routing root: {target.trace_output}"
        )
    record = {
        "version": 3,
        "session_id": session_id,
        "host": _normalize_host(host) if host else None,
        "task_group": target.task_group,
        "trace_output": str(target.trace_output),
        "branch_id": target.branch_id,
        "bound_cwd": str(event.get("cwd") or ""),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic_retry(
        path,
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
    )
    _bind_route_branch(target, event, host=host)
    return target


def resolve_session_route(
    routing_root: Path,
    event: dict[str, Any],
    *,
    default_trace_output: Path,
    route_dir: str,
) -> SessionRoute | None:
    """Load a route only when it belongs to this conversation and scope."""
    session_id = event_session_id(event)
    if not session_id:
        return None
    path = _route_path(
        routing_root,
        session_id,
        default_trace_output,
        route_dir=route_dir,
    )
    try:
        record = json.loads(read_text_retry(path))
    except (OSError, json.JSONDecodeError):
        return None
    if record.get("version") != 1:
        return None
    if record.get("default_scope") != _scope_fingerprint(default_trace_output):
        return None
    try:
        task_group = validate_scope_id(
            str(record.get("task_group") or ""),
            label="task_group",
        )
        trace_output = Path(str(record["trace_output"])).expanduser().resolve()
    except (KeyError, TypeError, ValueError, OSError):
        return None
    if not _is_within(trace_output, routing_root):
        return None
    return SessionRoute(
        task_group=task_group,
        trace_output=trace_output,
        branch_id=str(record.get("branch_id") or "").strip() or None,
    )


def create_fresh_session_route(
    routing_root: Path,
    event: dict[str, Any],
    *,
    default_trace_output: Path,
    project_scope: str,
    project_id: str | None,
    route_dir: str,
    fresh_dir: str,
    host_label: str,
    host: str,
) -> SessionRoute:
    """Create an idempotent MAST-from-zero route for one conversation."""
    session_id = event_session_id(event)
    if not session_id:
        raise ValueError(f"a {host_label} conversation id is required to start fresh")
    existing = resolve_session_route(
        routing_root,
        event,
        default_trace_output=default_trace_output,
        route_dir=route_dir,
    )
    if existing:
        return existing

    digest = hashlib.sha256(
        f"{session_id}\0{_normalized(default_trace_output)}".encode(
            "utf-8", "replace"
        )
    ).hexdigest()[:12]
    task_group = f"fresh-{digest}"
    validate_scope_id(task_group, label="task_group")
    root = Path(routing_root).expanduser().resolve()
    if project_scope == "auto":
        trace_output = project_program_path(
            root,
            cwd=event.get("cwd"),
            task_group=task_group,
            project_id=project_id,
        )
    else:
        trace_output = root / fresh_dir / task_group / "program"
    branch_id = f"{_normalize_host(host)}-branch-{digest}"
    record = {
        "version": 1,
        "default_scope": _scope_fingerprint(default_trace_output),
        "task_group": task_group,
        "trace_output": str(trace_output),
        "mode": "mast_fresh",
        "branch_id": branch_id,
    }
    path = _route_path(
        root,
        session_id,
        default_trace_output,
        route_dir=route_dir,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic_retry(
        path,
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
    )
    route = SessionRoute(
        task_group=task_group,
        trace_output=trace_output,
        branch_id=branch_id,
    )
    _bind_route_branch(route, event, host=host)
    return route


def _route_path(
    routing_root: Path,
    session_id: str,
    default_trace_output: Path,
    *,
    route_dir: str,
) -> Path:
    digest = hashlib.sha256(
        f"{session_id}\0{_normalized(default_trace_output)}".encode(
            "utf-8", "replace"
        )
    ).hexdigest()
    return Path(routing_root).expanduser().resolve() / route_dir / f"{digest}.json"


def _conversation_scope_path(
    routing_root: Path,
    session_id: str,
    *,
    scope_dir: str,
) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8", "replace")).hexdigest()
    return Path(routing_root).expanduser().resolve() / scope_dir / f"{digest}.json"


def _read_scope_record(
    path: Path,
    routing_root: Path,
    *,
    host: str | None = None,
) -> SessionRoute | None:
    try:
        record = json.loads(read_text_retry(path))
    except (OSError, json.JSONDecodeError):
        return None
    if record.get("version") not in {1, 2, 3}:
        return None
    try:
        task_group = validate_scope_id(
            str(record.get("task_group") or ""),
            label="task_group",
        )
        trace_output = Path(str(record["trace_output"])).expanduser().resolve()
    except (KeyError, TypeError, ValueError, OSError):
        return None
    if not _is_within(trace_output, routing_root):
        return None
    if host:
        expected = _normalize_host(host)
        recorded = _normalize_host(record.get("host"))
        if recorded and recorded != expected:
            return None
        program_host = _program_host(trace_output)
        if program_host not in {"neutral", expected}:
            return None
    branch_id = str(record.get("branch_id") or "").strip() or None
    return SessionRoute(
        task_group=task_group,
        trace_output=trace_output,
        branch_id=branch_id,
    )


def _discover_selected_scope(
    routing_root: Path,
    session_id: str,
    *,
    state_dir: str,
    default_task_group: str,
    host: str | None = None,
) -> SessionRoute | None:
    safe_id = "".join(
        char if char.isalnum() or char in "._-" else "_"
        for char in session_id
    )
    candidates: list[tuple[int, int, SessionRoute]] = []
    for path in routing_root.rglob(f"{safe_id}.json"):
        if path.parent.name != state_dir:
            continue
        try:
            state = json.loads(read_text_retry(path))
            status = str((state.get("selection") or {}).get("status") or "")
            if status not in {"selected", "disabled"}:
                continue
            trace_output = path.parent.parent.resolve()
            if not _is_within(trace_output, routing_root):
                continue
            if host and _program_host(trace_output) != _normalize_host(host):
                continue
            task_group = _task_group_from_program(
                trace_output,
                default=default_task_group,
            )
            sequence = int(state.get("episode_sequence") or 0)
            modified = path.stat().st_mtime_ns
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        candidates.append(
            (
                sequence,
                modified,
                SessionRoute(
                    task_group=task_group,
                    trace_output=trace_output,
                    branch_id=_manifest_branch_id(trace_output),
                ),
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _task_group_from_program(path: Path, *, default: str) -> str:
    parent = Path(path).parent
    if parent.parent.name == "groups":
        try:
            return validate_scope_id(parent.name, label="task_group")
        except ValueError:
            pass
    return validate_scope_id(default, label="task_group")


def _scope_fingerprint(path: Path) -> str:
    return hashlib.sha256(
        _normalized(path).encode("utf-8", "replace")
    ).hexdigest()


def _conversation_branch_route(
    routing_root: Path,
    *,
    session_id: str,
    default_trace_output: Path,
    host: str | None,
) -> SessionRoute:
    """Allocate a deterministic, conversation-owned program path."""
    normalized_host = _normalize_host(host) or "interactive"
    default = Path(default_trace_output).expanduser().resolve()
    digest = hashlib.sha256(
        f"{normalized_host}\0{session_id}\0{_normalized(default)}".encode(
            "utf-8", "replace"
        )
    ).hexdigest()[:16]
    branch_id = f"{normalized_host}-branch-{digest}"
    task_group = validate_scope_id(branch_id, label="task_group")
    if default.name == "program" and default.parent.parent.name == "groups":
        trace_output = default.parent.parent / task_group / "program"
    else:
        trace_output = (
            Path(routing_root).expanduser().resolve()
            / ".adamast-conversation-branches"
            / task_group
            / "program"
        )
    return SessionRoute(
        task_group=task_group,
        trace_output=trace_output,
        branch_id=branch_id,
    )


def _bind_route_branch(
    route: SessionRoute,
    event: dict[str, Any],
    *,
    host: str | None,
) -> None:
    if not route.branch_id or not host:
        return
    session_id = event_session_id(event)
    if not session_id:
        return
    ProgramWorkspace(
        route.trace_output,
        repo_path=event.get("cwd"),
    ).bind_conversation_branch(
        route.branch_id,
        conversation_id=session_id,
        host=_normalize_host(host),
    )


def _manifest_branch_id(trace_output: Path) -> str | None:
    try:
        manifest = json.loads(
            read_text_retry(Path(trace_output) / ".adamast-program.json")
        )
    except (OSError, json.JSONDecodeError):
        return None
    branch = manifest.get("branch")
    if not isinstance(branch, dict):
        return None
    return str(branch.get("branch_id") or "").strip() or None


def _program_host(trace_output: Path) -> str:
    manifest_path = Path(trace_output) / ".adamast-program.json"
    try:
        manifest = json.loads(read_text_retry(manifest_path))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    hosts = set()
    recorded = _normalize_host(manifest.get("host"))
    if recorded:
        hosts.add(recorded)
    source = manifest.get("source")
    if isinstance(source, dict):
        source_host = _normalize_host(source.get("host_id") or source.get("host"))
        if source_host:
            hosts.add(source_host)
    taxonomy_id = str(manifest.get("taxonomy_id") or "").strip().casefold()
    if taxonomy_id.startswith("tax-codex"):
        hosts.add("codex")
    if taxonomy_id.startswith("tax-claude"):
        hosts.add("claude_code")
    if (Path(trace_output) / ".adamast-codex").is_dir():
        hosts.add("codex")
    if (Path(trace_output) / ".adamast-claude-code").is_dir():
        hosts.add("claude_code")
    if not hosts:
        return "neutral"
    return next(iter(hosts)) if len(hosts) == 1 else "mixed"


def _normalize_host(value: object) -> str:
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(
        " ", "_"
    )
    if normalized in {"codex", "openai_codex"}:
        return "codex"
    if normalized in {"claude", "claude_code"}:
        return "claude_code"
    return ""


def _normalized(path: Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(parent).expanduser().resolve())
        return True
    except ValueError:
        return False
