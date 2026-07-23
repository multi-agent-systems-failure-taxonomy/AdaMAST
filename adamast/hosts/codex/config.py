"""Configuration for the Codex hook integration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from adamast.core import store
from adamast.core.traces import DEFAULT_TRACE_ROOT
from adamast.core.project_scope import (
    host_task_group,
    project_program_path,
    validate_scope_id,
)
from adamast.hosts.codex.session_routes import (
    create_fresh_session_route,
    resolve_conversation_scope,
    resolve_session_route,
)

CODEX_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "PostToolUse",
)
MATCHER_EVENTS = {"SessionStart", "SubagentStop", "PostToolUse"}


@dataclass(frozen=True)
class CodexHookSpec:
    """Registration policy for one Codex hook event."""

    event: str
    enabled: bool = True
    matchers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.event not in CODEX_HOOK_EVENTS:
            raise ValueError(
                f"Codex hook event {self.event!r} must be one of "
                f"{CODEX_HOOK_EVENTS}"
            )
        if self.matchers and self.event not in MATCHER_EVENTS:
            raise ValueError(f"Codex hook event {self.event!r} has no matcher")
        normalized = tuple(str(item).strip() for item in self.matchers)
        if any(not item for item in normalized):
            raise ValueError("Codex hook matcher cannot be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Codex hook matchers must be unique per event")
        object.__setattr__(self, "matchers", normalized)

    def to_dict(self) -> dict:
        data = {"enabled": self.enabled}
        if self.event in MATCHER_EVENTS:
            data["matchers"] = list(self.matchers)
        return data

    @classmethod
    def from_value(
        cls,
        event: str,
        value,
        *,
        default: "CodexHookSpec | None" = None,
    ) -> "CodexHookSpec":
        default = default or default_hook(event)
        if isinstance(value, bool):
            return cls(event=event, enabled=value, matchers=default.matchers)
        if isinstance(value, list | tuple | str):
            return cls(event=event, enabled=True, matchers=_matchers(value))
        if isinstance(value, dict):
            return cls(
                event=event,
                enabled=bool(value.get("enabled", default.enabled)),
                matchers=_matchers(value.get("matchers", default.matchers)),
            )
        raise ValueError(f"codex_hooks.{event} must be a bool, list, or object")


def default_hook(event: str) -> CodexHookSpec:
    matcher = {
        "SessionStart": ("startup|resume|compact",),
        "SubagentStop": ("*",),
        "PostToolUse": ("Bash|Edit|Write|apply_patch",),
    }.get(event, ())
    return CodexHookSpec(event=event, enabled=True, matchers=matcher)


def default_hooks() -> tuple[CodexHookSpec, ...]:
    return tuple(default_hook(event) for event in CODEX_HOOK_EVENTS)


def parse_codex_hooks(value=None) -> tuple[CodexHookSpec, ...]:
    defaults = {spec.event: spec for spec in default_hooks()}
    if value is None:
        return tuple(defaults[event] for event in CODEX_HOOK_EVENTS)
    if isinstance(value, dict):
        unknown = set(value) - set(CODEX_HOOK_EVENTS)
        if unknown:
            raise ValueError(f"unknown codex_hooks event(s): {sorted(unknown)}")
        merged = dict(defaults)
        for event, event_value in value.items():
            merged[event] = CodexHookSpec.from_value(
                event,
                event_value,
                default=defaults[event],
            )
        return tuple(merged[event] for event in CODEX_HOOK_EVENTS)
    if isinstance(value, list | tuple):
        merged = dict(defaults)
        for item in value:
            if not isinstance(item, dict) or "event" not in item:
                raise ValueError("codex_hooks list entries need an event")
            event = str(item["event"])
            merged[event] = CodexHookSpec.from_value(
                event,
                item,
                default=defaults.get(event),
            )
        return tuple(merged[event] for event in CODEX_HOOK_EVENTS)
    raise ValueError("codex_hooks must be an object or list")


def _matchers(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list | tuple):
        return tuple(str(item).strip() for item in value)
    raise ValueError("Codex hook matchers must be a string or list")


@dataclass(frozen=True)
class CodexConfig:
    trace_output: Path
    adamast_model: str
    store_dir: Path = store.DEFAULT_STORE_DIR
    trace_root: Path = DEFAULT_TRACE_ROOT
    inherit: str | None = None
    dashboard: bool = True
    openai_base_url: str | None = None
    openai_api_key_env: str | None = None
    max_retries: int = 3
    generation_threshold: int = 5
    generation_stops: bool = False
    skip_judge: bool = False
    k_init: int = 10
    k: int = 20
    refinement_stops: bool = False
    advanced_refinement: bool = False
    freeze: bool = False
    evidence_export: Path | None = None
    redact_traces: bool = True
    project_scope: str = "explicit"
    project_id: str | None = None
    task_group: str = "default"
    session_selector: str = "off"
    selector_surface: str = "browser"
    learning_backend: str = "provider"
    worker_model: str | None = None
    codex_cli_path: Path | None = None
    worker_timeout_seconds: int = 1800
    hooks: tuple[CodexHookSpec, ...] = field(default_factory=default_hooks)
    routing_root: Path | None = field(default=None, repr=False, compare=False)
    default_trace_output: Path | None = field(
        default=None, repr=False, compare=False
    )
    base_task_group: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not str(self.trace_output).strip():
            raise ValueError("Codex integration requires trace_output")
        if not str(self.adamast_model).strip():
            raise ValueError("Codex integration requires adamast_model")
        if self.project_scope not in {"explicit", "auto"}:
            raise ValueError("project_scope must be 'explicit' or 'auto'")
        if self.session_selector not in {"off", "prompt"}:
            raise ValueError("session_selector must be 'off' or 'prompt'")
        if self.selector_surface not in {"browser", "inline"}:
            raise ValueError("selector_surface must be 'browser' or 'inline'")
        if self.learning_backend not in {"provider", "codex_subagent"}:
            raise ValueError(
                "learning_backend must be 'provider' or 'codex_subagent'"
            )
        if self.learning_backend == "codex_subagent" and (
            self.generation_stops or self.refinement_stops
        ):
            raise ValueError(
                "codex_subagent learning requires background generation and refinement"
            )
        validate_scope_id(self.task_group, label="task_group")
        if self.base_task_group is not None:
            validate_scope_id(self.base_task_group, label="base_task_group")
        if self.project_id is not None:
            validate_scope_id(self.project_id, label="project_id")
        for name, value in (
            ("max_retries", self.max_retries),
            ("generation_threshold", self.generation_threshold),
            ("k_init", self.k_init),
            ("k", self.k),
            ("worker_timeout_seconds", self.worker_timeout_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        seen: set[str] = set()
        for spec in self.hooks:
            if not isinstance(spec, CodexHookSpec):
                raise TypeError("hooks entries must be CodexHookSpec instances")
            if spec.event in seen:
                raise ValueError(f"duplicate Codex hook event {spec.event!r}")
            seen.add(spec.event)

    @classmethod
    def load(cls, path: Path | str) -> "CodexConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        scoped = data.get("codex") if isinstance(data.get("codex"), dict) else {}
        inherit = data.get("inherit")
        if inherit in ("", "none"):
            inherit = None
        return cls(
            trace_output=Path(str(data["trace_output"])).expanduser().resolve(),
            adamast_model=str(data["adamast_model"]).strip(),
            store_dir=Path(
                data.get("store_dir", store.DEFAULT_STORE_DIR)
            ).expanduser().resolve(),
            trace_root=Path(
                data.get("trace_root", DEFAULT_TRACE_ROOT)
            ).expanduser().resolve(),
            inherit=str(inherit) if inherit is not None else None,
            dashboard=bool(data.get("dashboard", True)),
            openai_base_url=(
                str(data["openai_base_url"]).strip()
                if data.get("openai_base_url")
                else None
            ),
            openai_api_key_env=(
                str(data["openai_api_key_env"]).strip()
                if data.get("openai_api_key_env")
                else None
            ),
            max_retries=max(1, int(data.get("max_retries", 3))),
            generation_threshold=max(1, int(data.get("generation_threshold", 5))),
            generation_stops=bool(data.get("generation_stops", False)),
            skip_judge=bool(data.get("skip_judge", False)),
            k_init=max(1, int(data.get("k_init", 10))),
            k=max(1, int(data.get("k", 20))),
            refinement_stops=bool(data.get("refinement_stops", False)),
            advanced_refinement=bool(data.get("advanced_refinement", False)),
            freeze=bool(data.get("freeze", False)),
            evidence_export=(
                Path(str(data["evidence_export"])).expanduser().resolve()
                if data.get("evidence_export")
                else None
            ),
            redact_traces=bool(data.get("redact_traces", True)),
            project_scope=str(scoped.get("project_scope", "explicit")),
            project_id=(
                str(scoped["project_id"]).strip()
                if scoped.get("project_id")
                else None
            ),
            task_group=str(scoped.get("task_group", "default")),
            session_selector=str(scoped.get("session_selector", "off")),
            selector_surface=str(scoped.get("selector_surface", "browser")),
            learning_backend=str(scoped.get("learning_backend", "provider")),
            worker_model=(
                str(scoped["worker_model"]).strip()
                if scoped.get("worker_model")
                else None
            ),
            codex_cli_path=(
                Path(str(scoped["codex_cli_path"])).expanduser().resolve()
                if scoped.get("codex_cli_path")
                else None
            ),
            worker_timeout_seconds=max(
                1,
                int(scoped.get("worker_timeout_seconds", 1800)),
            ),
            hooks=parse_codex_hooks(scoped.get("hooks", data.get("codex_hooks"))),
        )

    def for_event(self, event: dict) -> "CodexConfig":
        """Return the event-scoped config used by user-level global hooks."""
        routing_root = self.routing_root or self.trace_output
        base_task_group = self.base_task_group or self.task_group
        routed_task_group = base_task_group
        if self.project_scope == "explicit":
            default_trace_output = self.default_trace_output or self.trace_output
        else:
            routed_task_group = host_task_group(base_task_group, host="codex")
            default_trace_output = project_program_path(
                routing_root,
                cwd=event.get("cwd"),
                task_group=routed_task_group,
                project_id=self.project_id,
            )
        scope = resolve_conversation_scope(
            routing_root,
            event,
            default_trace_output=default_trace_output,
            default_task_group=routed_task_group,
            isolate_conversation=self.session_selector == "prompt",
        )
        if scope:
            default_trace_output = scope.trace_output
            routed_task_group = scope.task_group
        route = resolve_session_route(
            routing_root,
            event,
            default_trace_output=default_trace_output,
        )
        return replace(
            self,
            trace_output=(
                route.trace_output if route else default_trace_output
            ),
            task_group=route.task_group if route else routed_task_group,
            routing_root=routing_root,
            default_trace_output=default_trace_output,
            base_task_group=base_task_group,
        )

    def start_fresh_conversation(self, event: dict) -> "CodexConfig":
        """Route this Codex conversation to a separate MAST task group."""
        scoped = self.for_event(event)
        create_fresh_session_route(
            scoped.routing_root or scoped.trace_output,
            event,
            default_trace_output=(
                scoped.default_trace_output or scoped.trace_output
            ),
            project_scope=scoped.project_scope,
            project_id=scoped.project_id,
        )
        return scoped.for_event(event)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "trace_output": str(self.trace_output),
            "adamast_model": self.adamast_model,
            "store_dir": str(self.store_dir),
            "trace_root": str(self.trace_root),
            "inherit": self.inherit or "none",
            "dashboard": self.dashboard,
            "openai_base_url": self.openai_base_url,
            "openai_api_key_env": self.openai_api_key_env,
            "max_retries": self.max_retries,
            "generation_threshold": self.generation_threshold,
            "generation_stops": self.generation_stops,
            "skip_judge": self.skip_judge,
            "k_init": self.k_init,
            "k": self.k,
            "refinement_stops": self.refinement_stops,
            "advanced_refinement": self.advanced_refinement,
            "freeze": self.freeze,
            "evidence_export": (
                str(self.evidence_export) if self.evidence_export else None
            ),
            "redact_traces": self.redact_traces,
            "codex": {
                "project_scope": self.project_scope,
                "project_id": self.project_id,
                "task_group": self.task_group,
                "session_selector": self.session_selector,
                "selector_surface": self.selector_surface,
                "learning_backend": self.learning_backend,
                "worker_model": self.worker_model,
                "codex_cli_path": (
                    str(self.codex_cli_path) if self.codex_cli_path else None
                ),
                "worker_timeout_seconds": self.worker_timeout_seconds,
                "hooks": {spec.event: spec.to_dict() for spec in self.hooks},
            },
        }
