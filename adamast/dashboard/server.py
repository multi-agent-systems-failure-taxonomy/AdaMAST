"""Persistent localhost monitor for runtime checkpoints and taxonomy data.

Unlike the blocking inheritance picker, this server stays alive until stopped.
The browser polls ``/api/monitor`` for project and conversation checkpoints;
``/api/taxonomy`` remains available for program-level consumers.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import resources
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from adamast.core import mast, store
from adamast.hosts.codex.transcript import codex_thread_title
from adamast.hosts.claude_code.transcript import claude_thread_title

from adamast.core.fsio import read_text_retry, write_text_atomic_retry
from adamast.core.lineage import TaxonomyLineage
from adamast.core.program import ProgramWorkspace

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_READY_TIMEOUT = 15.0
DASHBOARD_STATE = ".adamast-dashboard.json"
RUNTIME_EVIDENCE = ".adamast-runtime-evidence.json"
TASK_LABELS = ".adamast-task-labels.json"
# Reflection evidence/reasoning can be very long; the dashboard only needs a
# readable preview, so each field is clipped server-side before it is sent.
EVIDENCE_PREVIEW_CHARS = 8000
CODEX_STATE_DIR = ".adamast-codex"
CLAUDE_STATE_DIR = ".adamast-claude-code"
CONVERSATION_STATE_DIRS = (
    ("codex", CODEX_STATE_DIR),
    ("claude_code", CLAUDE_STATE_DIR),
)
_MANAGED_PROCESSES: dict[str, subprocess.Popen] = {}


def _text_asset(name: str) -> str:
    return (
        resources.files("adamast.dashboard")
        .joinpath("assets").joinpath(name)
        .read_text(encoding="utf-8")
    )


def _load_task_labels(root: Path) -> dict[str, Any]:
    """Optional session-id -> {label, correct} map written by a runner."""
    try:
        data = json.loads((root / TASK_LABELS).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _task_label(task_id: str, labels: dict[str, Any] | None) -> str:
    """Human label for a firing: the runner's task id + a solved marker, or a
    short prefix of the opaque session id when no map is available."""
    entry = (labels or {}).get(task_id)
    if isinstance(entry, dict) and entry.get("label"):
        correct = entry.get("correct")
        mark = " ✓" if correct is True else " ✗" if correct is False else ""
        return f"{entry['label']}{mark}"
    if isinstance(entry, str) and entry:
        return entry
    return task_id[:8] if task_id else "—"


def _clip(text: Any, limit: int = EVIDENCE_PREVIEW_CHARS) -> str:
    value = ("" if text is None else str(text)).strip()
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def current_taxonomy(
    workspace: ProgramWorkspace,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
) -> dict[str, Any]:
    """Resolve a branch head, or the legacy program's unambiguous successor."""
    store_dir = Path(store_dir)
    manifest = workspace.load()
    bound_id = manifest.get("taxonomy_id")
    if not bound_id:
        record = mast.MAST
        latest_id = mast.MAST_ID
    else:
        latest_id = (
            str(bound_id)
            if workspace.branch_id
            else TaxonomyLineage(store_dir).resolve_latest(str(bound_id))
        )
        record = store.fetch_by_id(latest_id, store_dir)

    # Runtime interaction evidence is program-local and taxonomy-version
    # scoped. It overlays the read-only taxonomy view without mutating the
    # global taxonomy record or the built-in MAST constant.
    labels = _load_task_labels(workspace.root)
    record = json.loads(json.dumps(record))
    _overlay_runtime_evidence(
        record,
        workspace.root / RUNTIME_EVIDENCE,
        latest_id,
        labels,
    )

    return {
        "program_id": manifest["program_id"],
        "taxonomy_id": latest_id,
        "bound_taxonomy_id": bound_id or mast.MAST_ID,
        "is_latest_successor": latest_id != (bound_id or mast.MAST_ID),
        "repo": (
            manifest.get("repo", "")
            if latest_id == mast.MAST_ID
            else record.get("repo", "")
        ),
        "domain": record.get("domain", ""),
        "codes": [_code_view(code, labels) for code in record["codes"]],
        "clean_checkpoints": _clean_checkpoints(
            workspace.root / RUNTIME_EVIDENCE,
            latest_id,
            labels,
        ),
    }


def _checkpoint_seq_map(
    evidence: dict[str, Any],
    taxonomy_id: str,
) -> dict[str, int]:
    """Number checkpoints for a taxonomy in chronological order."""
    ordered = sorted(
        (
            checkpoint
            for checkpoint in evidence.get("checkpoints", [])
            if isinstance(checkpoint, dict)
            and str(checkpoint.get("taxonomy_id")) == str(taxonomy_id)
        ),
        key=lambda checkpoint: checkpoint.get("timestamp") or 0,
    )
    sequence: dict[str, int] = {}
    for index, checkpoint in enumerate(ordered, 1):
        checkpoint_id = checkpoint.get("checkpoint_id")
        if checkpoint_id is not None and checkpoint_id not in sequence:
            sequence[str(checkpoint_id)] = index
    return sequence


def _overlay_runtime_evidence(
    record: dict[str, Any],
    evidence_path: Path,
    taxonomy_id: str,
    labels: dict[str, Any] | None = None,
) -> None:
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    sequence = _checkpoint_seq_map(evidence, taxonomy_id)
    codes = (
        evidence.get("taxonomies", {})
        .get(taxonomy_id, {})
        .get("codes", {})
    )
    if not isinstance(codes, dict):
        return
    for code in record.get("codes", []):
        runtime = codes.get(str(code.get("id")))
        if not isinstance(runtime, dict):
            continue
        code["fire_count"] = max(0, int(runtime.get("fire_count", 0)))
        task_firings = runtime.get("task_firings", {})
        if isinstance(task_firings, dict):
            code["task_firings"] = [
                {
                    "task_id": str(task_id),
                    "label": _task_label(str(task_id), labels),
                    "count": max(1, int(count)),
                }
                for task_id, count in sorted(task_firings.items())
            ]
        events = runtime.get("events")
        if isinstance(events, list):
            code["runtime_evidence"] = [
                {
                    "seq": sequence.get(str(event.get("checkpoint_id"))),
                    "timestamp": event.get("timestamp"),
                    "gate": event.get("gate"),
                    "task_id": event.get("task_id"),
                    "task_label": _task_label(
                        str(event.get("task_id", "")), labels
                    ),
                    "checkpoint_id": event.get("checkpoint_id"),
                    "evidence": _clip(event.get("evidence")),
                    "correlate": _clip(event.get("correlate")),
                    "decide": _clip(event.get("decide")),
                }
                for event in events
                if isinstance(event, dict)
            ]


def _clean_checkpoints(
    evidence_path: Path,
    taxonomy_id: str,
    labels: dict[str, Any] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return accepted checkpoints that did not fire a code."""
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    sequence = _checkpoint_seq_map(evidence, taxonomy_id)
    checkpoints: list[dict[str, Any]] = []
    for checkpoint in evidence.get("checkpoints", []):
        if not isinstance(checkpoint, dict):
            continue
        if str(checkpoint.get("taxonomy_id")) != str(taxonomy_id):
            continue
        if checkpoint.get("fired_codes"):
            continue
        checkpoints.append(
            {
                "seq": sequence.get(str(checkpoint.get("checkpoint_id"))),
                "timestamp": checkpoint.get("timestamp"),
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "gate": checkpoint.get("gate"),
                "task_id": checkpoint.get("task_id"),
                "task_label": _task_label(
                    str(checkpoint.get("task_id", "")),
                    labels,
                ),
                "none_apply": bool(checkpoint.get("none_apply")),
                "considered": list(checkpoint.get("considered_codes") or []),
                "observe": _clip(checkpoint.get("observe")),
                "correlate": _clip(checkpoint.get("correlate")),
                "decide": _clip(checkpoint.get("decide")),
            }
        )
    return checkpoints[-limit:]


def _code_view(
    code: dict[str, Any], labels: dict[str, Any] | None = None
) -> dict[str, Any]:
    task_firings = _task_firings(code, labels)
    fire_count = code.get("fire_count")
    if fire_count is None and task_firings:
        fire_count = sum(item["count"] for item in task_firings)
    primary = {
        "id",
        "name",
        "description",
        "fire_count",
        "task_ids",
        "task_firings",
        "runtime_evidence",
    }
    return {
        "code_id": code["id"],
        "name": code["name"],
        "description": code["description"],
        "fire_count": int(fire_count) if fire_count is not None else None,
        "task_firings": task_firings,
        "runtime_evidence": (
            code.get("runtime_evidence")
            if isinstance(code.get("runtime_evidence"), list)
            else []
        ),
        "fields": [
            {"name": str(key), "value": value}
            for key, value in code.items()
            if key not in primary
        ],
    }


def _task_firings(
    code: dict[str, Any], labels: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    raw = code.get("task_firings")
    if isinstance(raw, list):
        normalized = []
        for item in raw:
            if not isinstance(item, dict) or "task_id" not in item:
                continue
            task_id = str(item["task_id"])
            normalized.append(
                {
                    "task_id": task_id,
                    "label": item.get("label") or _task_label(task_id, labels),
                    "count": max(1, int(item.get("count", 1))),
                }
            )
        return normalized
    task_ids = code.get("task_ids")
    if isinstance(task_ids, list):
        return [
            {
                "task_id": str(task_id),
                "label": _task_label(str(task_id), labels),
                "count": 1,
            }
            for task_id in task_ids
        ]
    return []


def monitor_snapshot(
    monitor_root: Path | str,
    default_trace_output: Path | str,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    *,
    project_id: str | None = None,
    conversation_id: str | None = None,
    task_group: str | None = None,
    window: str = "all",
    limit: int = 50,
    since: float | None = None,
    until: float | None = None,
) -> dict[str, Any]:
    """Return the cross-project Codex and Claude checkpoint monitor model."""
    root = Path(monitor_root).expanduser().resolve()
    default_root = Path(default_trace_output).expanduser().resolve()
    programs = _discover_monitor_programs(root, default_root)
    projects: dict[str, dict[str, Any]] = {}
    conversations: list[dict[str, Any]] = []
    default_project_id = None
    default_program = None
    for program in programs:
        if program["root"] == default_root:
            default_project_id = program["project_id"]
            default_program = program
        project = projects.setdefault(
            program["project_id"],
            {
                "project_id": program["project_id"],
                "name": program["project_name"],
                "root": program["project_root"],
                "groups": set(),
                "conversation_count": 0,
                "running_count": 0,
            },
        )
        project["groups"].add(program["task_group"])
        program_conversations = _program_conversations(program)
        conversations.extend(program_conversations)
        project["conversation_count"] += len(program_conversations)
        project["running_count"] += sum(
            1 for item in program_conversations if item["status"] == "running"
        )

    project_list = []
    for project in projects.values():
        project["groups"] = sorted(project["groups"])
        if project["conversation_count"]:
            project_list.append(project)
    project_list.sort(key=lambda item: (item["name"].casefold(), item["project_id"]))

    selected_project = project_id if project_id in projects else default_project_id
    if selected_project is None and project_list:
        selected_project = project_list[0]["project_id"]
    valid_groups = set(projects.get(selected_project, {}).get("groups", set()))
    selected_group = (
        task_group
        if task_group and task_group != "all" and task_group in valid_groups
        else None
    )
    available_conversations = [
        item
        for item in conversations
        if item["project_id"] == selected_project
        and (
            selected_group is None
            or item["task_group"] == selected_group
        )
    ]
    available_conversations.sort(
        key=lambda item: (
            item["status"] != "running",
            -float(item.get("updated_at_unix") or 0),
            item["conversation_id"],
        )
    )
    selected_conversation = next(
        (
            item
            for item in available_conversations
            if item["conversation_id"] == conversation_id
        ),
        None,
    )
    if selected_conversation is None and available_conversations:
        selected_conversation = available_conversations[0]

    selected_program = None
    if selected_conversation:
        selected_program = next(
            (
                item
                for item in programs
                if str(item["root"]) == selected_conversation["program_root"]
            ),
            None,
        )
    if selected_program is None:
        selected_program = next(
            (item for item in programs if item["project_id"] == selected_project),
            default_program,
        )

    taxonomy = {
        "taxonomy_id": None,
        "domain": "",
        "codes": [],
    }
    checkpoints: list[dict[str, Any]] = []
    if selected_program is not None:
        workspace = ProgramWorkspace(selected_program["root"])
        taxonomy = current_taxonomy(workspace, store_dir)
        if selected_conversation:
            checkpoints = _conversation_checkpoints(
                selected_program,
                selected_conversation,
                taxonomy,
            )

    total_before_filter = len(checkpoints)
    now = time.time()
    if window == "24h":
        since = max(since or 0, now - 24 * 60 * 60)
    elif window == "7d":
        since = max(since or 0, now - 7 * 24 * 60 * 60)
    if since is not None:
        checkpoints = [item for item in checkpoints if item["timestamp"] >= since]
    if until is not None:
        checkpoints = [item for item in checkpoints if item["timestamp"] <= until]
    if window == "count":
        checkpoints = checkpoints[-max(1, min(int(limit), 1000)) :]

    fired_counts: dict[str, int] = {}
    for checkpoint in checkpoints:
        for code_id in checkpoint["fired_codes"]:
            fired_counts[code_id] = fired_counts.get(code_id, 0) + 1
    failure_modes = [
        {
            "code_id": code["code_id"],
            "name": code["name"],
            "description": code["description"],
            "checkpoint_count": fired_counts.get(code["code_id"], 0),
        }
        for code in taxonomy.get("codes", [])
    ]

    return {
        "generated_at": time.time(),
        "monitor_root": str(root),
        "projects": project_list,
        "conversations": [
            {key: value for key, value in item.items() if key != "program_root"}
            for item in available_conversations
        ],
        "selection": {
            "project_id": selected_project,
            "conversation_id": (
                selected_conversation["conversation_id"]
                if selected_conversation
                else None
            ),
            "task_group": (
                selected_conversation["task_group"]
                if selected_conversation
                else selected_program.get("task_group")
                if selected_program
                else None
            ),
            "task_group_filter": selected_group or "all",
        },
        "taxonomy": {
            "taxonomy_id": taxonomy.get("taxonomy_id"),
            "domain": taxonomy.get("domain", ""),
        },
        "failure_modes": failure_modes,
        "checkpoints": checkpoints,
        "summary": {
            "checkpoints": len(checkpoints),
            "total_checkpoints": total_before_filter,
            "failure_checkpoints": sum(
                1 for item in checkpoints if item["fired_codes"]
            ),
            "clean_checkpoints": sum(
                1 for item in checkpoints if not item["fired_codes"]
            ),
            "active_failure_modes": len(fired_counts),
        },
        "timeline": {
            "window": window,
            "limit": max(1, min(int(limit), 1000)),
            "since": since,
            "until": until,
        },
        "deferred": ["taxonomy refinement lineage"],
    }


def _discover_monitor_programs(
    monitor_root: Path,
    default_trace_output: Path,
) -> list[dict[str, Any]]:
    roots = {default_trace_output}
    if monitor_root.exists():
        roots.update(path.parent for path in monitor_root.rglob(".adamast-program.json"))
    programs = []
    for program_root in sorted(roots, key=lambda value: str(value).casefold()):
        manifest_path = program_root / ".adamast-program.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(read_text_retry(manifest_path))
        except (OSError, json.JSONDecodeError):
            continue
        project_id, task_group = _program_scope(program_root, monitor_root, manifest)
        project_path = ""
        for _host, state_dir in CONVERSATION_STATE_DIRS:
            state_root = program_root / state_dir
            if not state_root.is_dir():
                continue
            for state_path in state_root.glob("*.json"):
                try:
                    state = json.loads(read_text_retry(state_path))
                except (OSError, json.JSONDecodeError):
                    continue
                if state.get("cwd"):
                    project_path = str(state["cwd"])
                    break
            if project_path:
                break
        repo = str(manifest.get("repo") or "").strip()
        programs.append(
            {
                "root": program_root,
                "program_id": str(manifest.get("program_id") or program_root.name),
                "project_id": project_id,
                "project_name": repo or project_id.rsplit("-", 1)[0],
                "project_root": project_path or str(program_root),
                "task_group": task_group,
            }
        )
    return programs


def _program_scope(
    program_root: Path,
    monitor_root: Path,
    manifest: dict[str, Any],
) -> tuple[str, str]:
    try:
        parts = program_root.relative_to(monitor_root).parts
    except ValueError:
        parts = ()
    if len(parts) >= 5 and parts[0] == "projects" and parts[2] == "groups":
        return parts[1], parts[3]
    digest = hashlib.sha256(str(program_root).encode("utf-8", "replace")).hexdigest()[:12]
    label = str(manifest.get("repo") or program_root.parent.name or "project")
    slug = "".join(char if char.isalnum() or char in "._-" else "-" for char in label)
    return f"{slug.strip('-._') or 'project'}-{digest}", "default"


def _program_conversations(program: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(program["root"])
    evidence = _read_json(root / RUNTIME_EVIDENCE)
    checkpoints = [
        item for item in evidence.get("checkpoints", []) if isinstance(item, dict)
    ]
    by_conversation: dict[str, list[dict[str, Any]]] = {}
    for checkpoint in checkpoints:
        conversation_id = str(
            checkpoint.get("conversation_id") or checkpoint.get("session_id") or ""
        )
        if conversation_id:
            by_conversation.setdefault(conversation_id, []).append(checkpoint)

    conversations: dict[str, dict[str, Any]] = {}
    for host, state_dir in CONVERSATION_STATE_DIRS:
        state_root = root / state_dir
        if not state_root.is_dir():
            continue
        for path in state_root.glob("*.json"):
            state = _read_json(path)
            conversation_id = str(
                state.get("conversation_id") or state.get("session_id") or ""
            )
            if not conversation_id:
                continue
            related = by_conversation.get(conversation_id, [])
            latest_checkpoint = max(
                (float(item.get("timestamp") or 0) for item in related),
                default=0,
            )
            updated = max(_state_timestamp(state.get("updated_at")), latest_checkpoint)
            title = str(
                _host_conversation_title(host, conversation_id, state)
                or state.get("conversation_title")
                or state.get("episode_task")
                or f"Conversation {conversation_id[:8]}"
            ).strip()
            conversations[conversation_id] = {
                "conversation_id": conversation_id,
                "title": _clip(title, 120),
                "host": host,
                "host_label": _host_label(host),
                "project_id": program["project_id"],
                "task_group": state.get("task_group") or program["task_group"],
                "status": "past" if state.get("finished") else "running",
                "episode_count": int(state.get("episode_sequence") or 0),
                "checkpoint_count": len(related),
                "created_at": state.get("created_at"),
                "updated_at": state.get("updated_at"),
                "updated_at_unix": updated,
                "program_root": str(root),
            }
    for conversation_id, related in by_conversation.items():
        if conversation_id in conversations:
            continue
        latest = max(float(item.get("timestamp") or 0) for item in related)
        conversations[conversation_id] = {
            "conversation_id": conversation_id,
            "title": f"Conversation {conversation_id[:8]}",
            "host": _evidence_host(related),
            "host_label": _host_label(_evidence_host(related)),
            "project_id": program["project_id"],
            "task_group": related[-1].get("task_group") or program["task_group"],
            "status": "past",
            "episode_count": max(
                (int(item.get("episode_sequence") or 0) for item in related),
                default=0,
            ),
            "checkpoint_count": len(related),
            "created_at": None,
            "updated_at": None,
            "updated_at_unix": latest,
            "program_root": str(root),
        }
    return list(conversations.values())


def _conversation_checkpoints(
    program: dict[str, Any],
    conversation: dict[str, Any],
    taxonomy: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence = _read_json(Path(program["root"]) / RUNTIME_EVIDENCE)
    code_lookup = {
        str(code["code_id"]): {
            "name": code.get("name") or "Unnamed failure mode",
            "description": code.get("description") or "",
        }
        for code in taxonomy.get("codes", [])
    }
    assignment_map: dict[str, list[dict[str, Any]]] = {}
    for taxonomy_evidence in evidence.get("taxonomies", {}).values():
        if not isinstance(taxonomy_evidence, dict):
            continue
        for code_id, code in taxonomy_evidence.get("codes", {}).items():
            if not isinstance(code, dict):
                continue
            for event in code.get("events", []):
                if not isinstance(event, dict) or not event.get("checkpoint_id"):
                    continue
                assignment_map.setdefault(str(event["checkpoint_id"]), []).append(
                    {
                        "code_id": str(code_id),
                        "name": code_lookup.get(str(code_id), {}).get("name", str(code_id)),
                        "evidence": _clip(event.get("evidence")),
                    }
                )

    raw = []
    for checkpoint in evidence.get("checkpoints", []):
        if not isinstance(checkpoint, dict):
            continue
        checkpoint_conversation = str(
            checkpoint.get("conversation_id") or checkpoint.get("session_id") or ""
        )
        if checkpoint_conversation != conversation["conversation_id"]:
            continue
        raw.append(checkpoint)
    raw.sort(key=lambda item: float(item.get("timestamp") or 0))
    normalized = []
    for sequence, checkpoint in enumerate(raw, 1):
        checkpoint_id = str(checkpoint.get("checkpoint_id") or f"checkpoint-{sequence}")
        fired = [str(value) for value in checkpoint.get("fired_codes") or []]
        episode = int(checkpoint.get("episode_sequence") or sequence)
        normalized.append(
            {
                "seq": sequence,
                "checkpoint_id": checkpoint_id,
                "timestamp": float(checkpoint.get("timestamp") or 0),
                "project_id": program["project_id"],
                "task_group": checkpoint.get("task_group") or program["task_group"],
                "conversation_id": conversation["conversation_id"],
                "conversation_title": conversation["title"],
                "host": conversation.get("host", "unknown"),
                "host_label": conversation.get("host_label", "Agent runtime"),
                "episode_sequence": episode,
                "turn_label": f"Turn {episode}",
                "turn_id": checkpoint.get("turn_id"),
                "gate": str(checkpoint.get("gate") or "checkpoint"),
                "gate_status": checkpoint.get("gate_status") or "RECORDED",
                "checkpoint": _clip(checkpoint.get("observe")),
                "observe": _clip(checkpoint.get("observe")),
                "correlate": _clip(checkpoint.get("correlate")),
                "decide": _clip(checkpoint.get("decide")),
                "evidence": _clip(checkpoint.get("correlate")),
                "next_action": _clip(checkpoint.get("decide")),
                "taxonomy_id": checkpoint.get("taxonomy_id"),
                "none_apply": bool(checkpoint.get("none_apply")),
                "considered_codes": [
                    str(value) for value in checkpoint.get("considered_codes") or []
                ],
                "fired_codes": fired,
                "failure_modes": [
                    {
                        "code_id": code_id,
                        "name": code_lookup.get(code_id, {}).get("name", code_id),
                    }
                    for code_id in fired
                ],
                "map": assignment_map.get(checkpoint_id, []),
                "agent_id": checkpoint.get("agent_id"),
                "agent_type": checkpoint.get("agent_type"),
                "source": checkpoint.get("source"),
            }
        )
    return normalized


def _host_conversation_title(
    host: str,
    conversation_id: str,
    state: dict[str, Any],
) -> str:
    if host == "codex":
        return codex_thread_title(conversation_id)
    if host == "claude_code":
        return claude_thread_title(
            conversation_id,
            transcript_path=state.get("transcript_path"),
        )
    return ""


def _evidence_host(checkpoints: list[dict[str, Any]]) -> str:
    values = " ".join(
        str(item.get(key) or "")
        for item in checkpoints
        for key in ("agent_type", "source")
    ).casefold()
    if "codex" in values:
        return "codex"
    if "claude" in values:
        return "claude_code"
    return "unknown"


def _host_label(host: str) -> str:
    return {
        "codex": "Codex",
        "claude_code": "Claude Code",
    }.get(host, "Agent runtime")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(read_text_retry(path))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _state_timestamp(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0
    return 0


def build_server(
    trace_output: Path | str,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    shutdown_token: str | None = None,
    monitor_root: Path | str | None = None,
) -> ThreadingHTTPServer:
    """Build the persistent dashboard server without starting it."""
    workspace = ProgramWorkspace(trace_output)
    store_dir = Path(store_dir)
    monitor_mode = monitor_root is not None
    monitor_root = Path(monitor_root or trace_output).expanduser().resolve()
    monitor_id = _monitor_id(monitor_root)
    page = _CODEX_MONITOR_PAGE if monitor_mode else _PAGE

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def _send(
            self,
            body: bytes,
            *,
            status: int = 200,
            content_type: str,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/":
                self._send(
                    page.encode("utf-8"),
                    content_type="text/html; charset=utf-8",
                )
                return
            if path == "/api/taxonomy":
                try:
                    payload = current_taxonomy(workspace, store_dir)
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self._send(
                        body,
                        content_type="application/json; charset=utf-8",
                    )
                except Exception as exc:
                    body = json.dumps(
                        {"error": str(exc)}, ensure_ascii=False
                    ).encode("utf-8")
                    self._send(
                        body,
                        status=500,
                        content_type="application/json; charset=utf-8",
                    )
                return
            if path == "/api/monitor":
                try:
                    query = parse_qs(parsed.query)
                    payload = monitor_snapshot(
                        monitor_root,
                        workspace.root,
                        store_dir,
                        project_id=_query_value(query, "project"),
                        conversation_id=_query_value(query, "conversation"),
                        task_group=_query_value(query, "group"),
                        window=_query_value(query, "window") or "all",
                        limit=_query_int(query, "limit", 50),
                        since=_query_float(query, "since"),
                        until=_query_float(query, "until"),
                    )
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self._send(
                        body,
                        content_type="application/json; charset=utf-8",
                    )
                except Exception as exc:
                    body = json.dumps(
                        {"error": str(exc)}, ensure_ascii=False
                    ).encode("utf-8")
                    self._send(
                        body,
                        status=500,
                        content_type="application/json; charset=utf-8",
                    )
                return
            if path == "/api/health":
                body = json.dumps(
                    {
                        "program_id": workspace.program_id,
                        "monitor_id": monitor_id,
                        "view": "monitor" if monitor_mode else "taxonomy",
                        "status": "ok",
                    }
                ).encode("utf-8")
                self._send(
                    body,
                    content_type="application/json; charset=utf-8",
                )
                return
            self._send(
                b'{"error":"not found"}',
                status=404,
                content_type="application/json; charset=utf-8",
            )

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path != "/api/shutdown" or shutdown_token is None:
                self._send(
                    b'{"error":"not found"}',
                    status=404,
                    content_type="application/json; charset=utf-8",
                )
                return
            if self.headers.get("X-AdaMAST-Token") != shutdown_token:
                self._send(
                    b'{"error":"forbidden"}',
                    status=403,
                    content_type="application/json; charset=utf-8",
                )
                return
            self._send(
                b'{"status":"stopping"}',
                content_type="application/json; charset=utf-8",
            )
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    return ThreadingHTTPServer((host, port), Handler)


def _query_value(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name) or []
    value = str(values[0]).strip() if values else ""
    return value or None


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(_query_value(query, name) or default)
    except ValueError:
        return default


def _query_float(query: dict[str, list[str]], name: str) -> float | None:
    value = _query_value(query, name)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _monitor_id(root: Path | str) -> str:
    normalized = os.path.normcase(str(Path(root).expanduser().resolve()))
    return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()[:16]


def run_dashboard(
    trace_output: Path | str,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = True,
    on_serving=None,
    monitor_root: Path | str | None = None,
) -> None:
    """Serve until interrupted; taxonomy changes are picked up live."""
    server = build_server(
        trace_output,
        store_dir,
        host,
        port,
        monitor_root=monitor_root,
    )
    actual_port = int(server.server_address[1])
    url = f"http://{host}:{actual_port}/"
    if on_serving is not None:
        on_serving(host, actual_port)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"WARNING: dashboard is binding to {host!r} and serves trace and "
            "evidence text UNAUTHENTICATED. Do not expose it beyond localhost "
            "without an external authentication layer.",
            file=sys.stderr,
        )
    print(f"AdaMAST taxonomy dashboard: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def start_dashboard_thread(
    trace_output: Path | str,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    host: str = DEFAULT_HOST,
    port: int = 0,
    *,
    monitor_root: Path | str | None = None,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Start a daemon dashboard for embedding and tests."""
    server = build_server(
        trace_output,
        store_dir,
        host,
        port,
        monitor_root=monitor_root,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _ready_timeout() -> float:
    """Readiness budget for the spawned dashboard, in seconds.

    A cold interpreter on a busy host can need well over five seconds just to
    import the package, so the default is generous; ``ADAMAST_DASHBOARD_TIMEOUT``
    overrides it (floored at one second).
    """
    raw = os.environ.get("ADAMAST_DASHBOARD_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_READY_TIMEOUT
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_READY_TIMEOUT


def ensure_dashboard(
    workspace: ProgramWorkspace,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    *,
    timeout: float | None = None,
    monitor_root: Path | str | None = None,
    project_id: str | None = None,
    conversation_id: str | None = None,
) -> str | None:
    """Start or reuse one managed monitor and return a scoped view URL."""
    if timeout is None:
        timeout = _ready_timeout()
    if os.environ.get("ADAMAST_DISABLE_DASHBOARD", "").lower() in {
        "1", "true", "yes",
    }:
        return None
    monitor_mode = monitor_root is not None
    root = Path(monitor_root or workspace.root).expanduser().resolve()
    monitor_id = _monitor_id(root)
    state_path = root / DASHBOARD_STATE
    with _dashboard_lock(root):
        state = _read_state(state_path)
        if state and _dashboard_is_live(
            state,
            program_id=(None if monitor_mode else workspace.program_id),
            monitor_id=(monitor_id if monitor_mode else None),
        ):
            return _scoped_dashboard_url(
                str(state["url"]),
                project_id=project_id,
                conversation_id=conversation_id,
            )
        state_path.unlink(missing_ok=True)
        token = secrets.token_urlsafe(24)
        command = [
            sys.executable,
            "-m",
            "adamast.dashboard.server",
            "--trace-output",
            str(workspace.root),
            "--store-dir",
            str(Path(store_dir)),
            "--port",
            "0",
            "--no-browser",
            "--managed-token",
            token,
            "--state-file",
            str(state_path),
        ]
        if monitor_mode:
            command.extend(["--monitor-root", str(root)])
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": str(Path(__file__).resolve().parents[2]),
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
            )
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
        _MANAGED_PROCESSES[str(state_path)] = process
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = _read_state(state_path)
            if state and _dashboard_is_live(
                state,
                program_id=(None if monitor_mode else workspace.program_id),
                monitor_id=(monitor_id if monitor_mode else None),
            ):
                return _scoped_dashboard_url(
                    str(state["url"]),
                    project_id=project_id,
                    conversation_id=conversation_id,
                )
            time.sleep(0.05)
    return None


def stop_dashboard_if_idle(workspace: ProgramWorkspace) -> bool:
    """Stop the managed dashboard once no task or learning job remains."""
    manifest = workspace.load()
    if manifest.get("active_sessions"):
        return False
    if manifest.get("generation", {}).get("state") == "running":
        return False
    if manifest.get("refinement", {}).get("state") == "running":
        return False
    return stop_dashboard(workspace)


def stop_dashboard(
    workspace: ProgramWorkspace,
    *,
    timeout: float = 3.0,
    monitor_root: Path | str | None = None,
) -> bool:
    root = Path(monitor_root or workspace.root).expanduser().resolve()
    state_path = root / DASHBOARD_STATE
    with _dashboard_lock(root):
        state = _read_state(state_path)
        if not state:
            return False
        try:
            request = Request(
                str(state["shutdown_url"]),
                method="POST",
                headers={"X-AdaMAST-Token": str(state["token"])},
            )
            with urlopen(request, timeout=timeout):
                pass
        except Exception:
            pass
        process = _MANAGED_PROCESSES.pop(str(state_path), None)
        if process is not None:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
        state_path.unlink(missing_ok=True)
        return True


def _dashboard_is_live(
    state: dict[str, Any],
    *,
    program_id: str | None = None,
    monitor_id: str | None = None,
) -> bool:
    try:
        with urlopen(str(state["health_url"]), timeout=0.5) as response:
            data = json.loads(response.read().decode("utf-8"))
        identity_matches = (
            data.get("view") == "monitor" and data.get("monitor_id") == monitor_id
            if monitor_id is not None
            else data.get("view") in {None, "taxonomy"}
            and data.get("program_id") == program_id
        )
        return identity_matches and data.get("status") == "ok"
    except Exception:
        return False


def _scoped_dashboard_url(
    base_url: str,
    *,
    project_id: str | None,
    conversation_id: str | None,
) -> str:
    query = {
        key: value
        for key, value in (
            ("project", project_id),
            ("conversation", conversation_id),
        )
        if value
    }
    return base_url + ("?" + urlencode(query) if query else "")


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(read_text_retry(path))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


class _dashboard_lock:
    def __init__(self, root: Path):
        self.path = root / ".dashboard.lock"

    def __enter__(self):
        deadline = time.monotonic() + 5
        while True:
            try:
                self.path.mkdir()
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for dashboard lock")
                time.sleep(0.05)

    def __exit__(self, *_args):
        try:
            self.path.rmdir()
        except FileNotFoundError:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Keep a live local view of AdaMAST runtime checkpoints."
    )
    parser.add_argument("--trace-output", "--trace_output", required=True)
    parser.add_argument("--store-dir", default=store.DEFAULT_STORE_DIR)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--monitor-root")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--managed-token")
    parser.add_argument("--state-file")
    args = parser.parse_args(argv)
    if args.managed_token:
        server = build_server(
            args.trace_output,
            args.store_dir,
            args.host,
            args.port,
            shutdown_token=args.managed_token,
            monitor_root=args.monitor_root,
        )
        actual_port = int(server.server_address[1])
        url = f"http://{args.host}:{actual_port}/"
        if not args.state_file:
            parser.error("--state-file is required with --managed-token")
        state_path = Path(args.state_file)
        write_text_atomic_retry(
            state_path,
            json.dumps(
                {
                    "pid": os.getpid(),
                    "url": url,
                    "monitor_id": _monitor_id(args.monitor_root or args.trace_output),
                    "health_url": f"{url}api/health",
                    "shutdown_url": f"{url}api/shutdown",
                    "token": args.managed_token,
                },
                indent=2,
            ) + "\n",
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
            state_path.unlink(missing_ok=True)
        return 0
    run_dashboard(
        args.trace_output,
        args.store_dir,
        args.host,
        args.port,
        open_browser=not args.no_browser,
        monitor_root=args.monitor_root,
    )
    return 0


_PAGE = _text_asset("dashboard.html")
_CODEX_MONITOR_PAGE = _text_asset("codex_monitor.html")


if __name__ == "__main__":
    raise SystemExit(main())
