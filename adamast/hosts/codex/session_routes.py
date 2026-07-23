"""Codex facade for shared durable interactive session routing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adamast.hosts.interactive.session_routes import (
    SessionRoute,
    event_session_id,
)
from adamast.hosts.interactive.session_routes import (
    resolve_conversation_scope as _resolve_conversation_scope,
)
from adamast.hosts.interactive.session_routes import (
    create_fresh_session_route as _create_fresh_session_route,
)
from adamast.hosts.interactive.session_routes import (
    resolve_session_route as _resolve_session_route,
)

ROUTE_DIR = ".adamast-codex-routes"
FRESH_DIR = ".adamast-codex-fresh"
SCOPE_DIR = ".adamast-codex-session-scopes"
STATE_DIR = ".adamast-codex"


def resolve_conversation_scope(
    routing_root: Path,
    event: dict[str, Any],
    *,
    default_trace_output: Path,
    default_task_group: str,
    isolate_conversation: bool = False,
) -> SessionRoute | None:
    return _resolve_conversation_scope(
        routing_root,
        event,
        default_trace_output=default_trace_output,
        default_task_group=default_task_group,
        scope_dir=SCOPE_DIR,
        state_dir=STATE_DIR,
        host_label="Codex",
        host="codex",
        isolate_conversation=isolate_conversation,
    )


def resolve_session_route(
    routing_root: Path,
    event: dict[str, Any],
    *,
    default_trace_output: Path,
) -> SessionRoute | None:
    return _resolve_session_route(
        routing_root,
        event,
        default_trace_output=default_trace_output,
        route_dir=ROUTE_DIR,
    )


def create_fresh_session_route(
    routing_root: Path,
    event: dict[str, Any],
    *,
    default_trace_output: Path,
    project_scope: str,
    project_id: str | None,
) -> SessionRoute:
    return _create_fresh_session_route(
        routing_root,
        event,
        default_trace_output=default_trace_output,
        project_scope=project_scope,
        project_id=project_id,
        route_dir=ROUTE_DIR,
        fresh_dir=FRESH_DIR,
        host_label="Codex",
        host="codex",
    )


__all__ = [
    "FRESH_DIR",
    "ROUTE_DIR",
    "SCOPE_DIR",
    "STATE_DIR",
    "SessionRoute",
    "create_fresh_session_route",
    "event_session_id",
    "resolve_conversation_scope",
    "resolve_session_route",
]
