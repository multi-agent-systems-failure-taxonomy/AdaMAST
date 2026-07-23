"""Configuration for the Claude Code runtime skin."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from importlib.resources import files
from pathlib import Path

from adamast.core import store
from adamast.core.project_scope import (
    host_task_group,
    project_program_path,
    validate_scope_id,
)
from adamast.core.traces import DEFAULT_TRACE_ROOT

from .session_routes import (
    create_fresh_session_route,
    resolve_conversation_scope,
    resolve_session_route,
)

_HOOK_EVENTS = json.loads(
    files(__package__).joinpath("assets").joinpath("hook_events.json").read_text(
        encoding="utf-8"
    )
)

CUSTOM_HOOK_MODES = tuple(_HOOK_EVENTS["custom_hook_modes"])
CUSTOM_HOOK_CHECKPOINT_KEYS = tuple(_HOOK_EVENTS["custom_hook_checkpoint_keys"])
CUSTOM_HOOK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
BUILT_IN_HOOK_EVENTS = tuple(_HOOK_EVENTS["built_in_hook_events"])
BUILT_IN_MATCHER_EVENTS = tuple(_HOOK_EVENTS["built_in_matcher_events"])
CLAUDE_CODE_EVENTS = tuple(_HOOK_EVENTS["claude_code_events"])


@dataclass(frozen=True)
class CustomHookSpec:
    """A user-declared hook bound to the reflection-or-nudge runtime.

    Custom hooks let a project register a Claude Code event (PreToolUse,
    UserPromptSubmit, etc.) and have the same reflection<->refinement loop
    fire on it without writing any new Python. The dispatcher routes
    matching events here based on ``name``; the installer registers
    ``settings.local.json`` entries that point at this skin.
    """

    name: str
    event: str
    mode: str = "blocking"
    matcher: str | None = None
    command_pattern: str | None = None
    checkpoint_key: str = "tool_use_id"

    def __post_init__(self) -> None:
        if not CUSTOM_HOOK_NAME_RE.match(self.name):
            raise ValueError(
                f"custom hook name {self.name!r} must match "
                f"{CUSTOM_HOOK_NAME_RE.pattern}"
            )
        if self.event not in CLAUDE_CODE_EVENTS:
            raise ValueError(
                f"custom hook event {self.event!r} is not a Claude Code "
                f"hook event; expected one of {CLAUDE_CODE_EVENTS}"
            )
        if self.mode not in CUSTOM_HOOK_MODES:
            raise ValueError(
                f"custom hook mode {self.mode!r} must be one of "
                f"{CUSTOM_HOOK_MODES}"
            )
        if self.matcher is not None and not str(self.matcher).strip():
            raise ValueError("custom hook matcher cannot be empty string")
        if self.command_pattern is not None:
            pattern = str(self.command_pattern).strip()
            if not pattern:
                raise ValueError("custom hook command_pattern cannot be empty string")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"custom hook command_pattern is invalid regex: {exc}"
                ) from exc
            object.__setattr__(self, "command_pattern", pattern)
        if self.checkpoint_key not in CUSTOM_HOOK_CHECKPOINT_KEYS:
            raise ValueError(
                f"custom hook checkpoint_key {self.checkpoint_key!r} must "
                f"be one of {CUSTOM_HOOK_CHECKPOINT_KEYS}"
            )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "event": self.event,
            "mode": self.mode,
            "matcher": self.matcher,
            "command_pattern": self.command_pattern,
            "checkpoint_key": self.checkpoint_key,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CustomHookSpec":
        matcher = data.get("matcher")
        return cls(
            name=str(data["name"]),
            event=str(data["event"]),
            mode=str(data.get("mode", "blocking")),
            matcher=str(matcher) if matcher else None,
            command_pattern=(
                str(data["command_pattern"])
                if data.get("command_pattern")
                else None
            ),
            checkpoint_key=str(data.get("checkpoint_key", "tool_use_id")),
        )


@dataclass(frozen=True)
class BuiltInHookSpec:
    """Registration policy for one built-in Claude Code hook event."""

    event: str
    enabled: bool = True
    matchers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.event not in BUILT_IN_HOOK_EVENTS:
            raise ValueError(
                f"built-in hook event {self.event!r} must be one of "
                f"{BUILT_IN_HOOK_EVENTS}"
            )
        if self.matchers and self.event not in BUILT_IN_MATCHER_EVENTS:
            raise ValueError(
                f"built-in hook event {self.event!r} does not support matchers"
            )
        normalized = tuple(str(item).strip() for item in self.matchers)
        if any(not item for item in normalized):
            raise ValueError("built-in hook matcher cannot be empty string")
        if len(set(normalized)) != len(normalized):
            raise ValueError("built-in hook matchers must be unique per event")
        object.__setattr__(self, "matchers", normalized)

    def to_dict(self) -> dict:
        data = {"enabled": self.enabled}
        if self.event in BUILT_IN_MATCHER_EVENTS:
            data["matchers"] = list(self.matchers)
        return data

    @classmethod
    def from_value(
        cls,
        event: str,
        value,
        *,
        default: "BuiltInHookSpec | None" = None,
    ) -> "BuiltInHookSpec":
        default = default or default_built_in_hook(event)
        if isinstance(value, bool):
            return cls(event=event, enabled=value, matchers=default.matchers)
        if isinstance(value, list | tuple):
            return cls(event=event, enabled=True, matchers=_matchers(value))
        if isinstance(value, dict):
            enabled = bool(value.get("enabled", default.enabled))
            raw_matchers = value.get("matchers", default.matchers)
            return cls(event=event, enabled=enabled, matchers=_matchers(raw_matchers))
        raise ValueError(
            f"built_in_hooks.{event} must be a bool, matcher list, or object"
        )


def default_built_in_hook(event: str) -> BuiltInHookSpec:
    return BuiltInHookSpec(
        event=event,
        enabled=True,
        matchers=("*",) if event in BUILT_IN_MATCHER_EVENTS else (),
    )


def default_built_in_hooks() -> tuple[BuiltInHookSpec, ...]:
    return tuple(default_built_in_hook(event) for event in BUILT_IN_HOOK_EVENTS)


def parse_built_in_hooks(value=None) -> tuple[BuiltInHookSpec, ...]:
    defaults = {spec.event: spec for spec in default_built_in_hooks()}
    if value is None:
        return tuple(defaults[event] for event in BUILT_IN_HOOK_EVENTS)
    if isinstance(value, dict):
        unknown = set(value) - set(BUILT_IN_HOOK_EVENTS)
        if unknown:
            raise ValueError(f"unknown built_in_hooks event(s): {sorted(unknown)}")
        merged = dict(defaults)
        for event, event_value in value.items():
            merged[event] = BuiltInHookSpec.from_value(
                event,
                event_value,
                default=defaults[event],
            )
        return tuple(merged[event] for event in BUILT_IN_HOOK_EVENTS)
    if isinstance(value, list | tuple):
        merged = dict(defaults)
        for item in value:
            if not isinstance(item, dict) or "event" not in item:
                raise ValueError(
                    "built_in_hooks list entries must be objects with an event"
                )
            event = str(item["event"])
            merged[event] = BuiltInHookSpec.from_value(
                event,
                item,
                default=defaults.get(event),
            )
        return tuple(merged[event] for event in BUILT_IN_HOOK_EVENTS)
    raise ValueError("built_in_hooks must be an object or list")


def _matchers(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list | tuple):
        return tuple(str(item).strip() for item in value)
    raise ValueError("built-in hook matchers must be a string or list")


@dataclass(frozen=True)
class ClaudeCodeConfig:
    trace_output: Path
    adamast_model: str
    store_dir: Path = store.DEFAULT_STORE_DIR
    trace_root: Path = DEFAULT_TRACE_ROOT
    inherit: str | None = None
    dashboard: bool = True
    openai_base_url: str | None = None
    openai_api_key_env: str | None = None
    max_retries: int = 3
    format_retries: int = 2
    repair_rounds: int | None = None
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
    failure_throttle_calls: int = 5
    failure_recency_seconds: int = 30
    project_scope: str = "explicit"
    project_id: str | None = None
    task_group: str = "default"
    session_selector: str = "off"
    selector_surface: str = "inline"
    learning_backend: str = "provider"
    worker_model: str | None = None
    claude_cli_path: Path | None = None
    worker_timeout_seconds: int = 1800
    built_in_hooks: tuple[BuiltInHookSpec, ...] = field(
        default_factory=default_built_in_hooks
    )
    custom_hooks: tuple[CustomHookSpec, ...] = field(default_factory=tuple)
    routing_root: Path | None = field(default=None, repr=False, compare=False)
    default_trace_output: Path | None = field(
        default=None, repr=False, compare=False
    )
    base_task_group: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not str(self.adamast_model).strip():
            raise ValueError("Claude Code integration requires adamast_model")
        if self.project_scope not in {"explicit", "auto"}:
            raise ValueError("project_scope must be 'explicit' or 'auto'")
        if self.session_selector not in {"off", "prompt"}:
            raise ValueError("session_selector must be 'off' or 'prompt'")
        if self.selector_surface not in {"browser", "inline"}:
            raise ValueError("selector_surface must be 'browser' or 'inline'")
        if self.learning_backend not in {"provider", "claude_subagent"}:
            raise ValueError(
                "learning_backend must be 'provider' or 'claude_subagent'"
            )
        if self.learning_backend == "claude_subagent" and (
            self.generation_stops or self.refinement_stops
        ):
            raise ValueError(
                "claude_subagent learning requires background generation and refinement"
            )
        validate_scope_id(self.task_group, label="task_group")
        if self.base_task_group is not None:
            validate_scope_id(self.base_task_group, label="base_task_group")
        if self.project_id is not None:
            validate_scope_id(self.project_id, label="project_id")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        # max_retries is the legacy shared knob; it maps to the substantive
        # repair budget when repair_rounds is not set explicitly.
        if self.repair_rounds is None:
            object.__setattr__(self, "repair_rounds", self.max_retries)
        if self.repair_rounds < 0:
            raise ValueError("repair_rounds cannot be negative")
        if self.format_retries < 1:
            raise ValueError("format_retries must be positive")
        for name, value in (
            ("generation_threshold", self.generation_threshold),
            ("k_init", self.k_init),
            ("k", self.k),
            ("failure_throttle_calls", self.failure_throttle_calls),
            ("worker_timeout_seconds", self.worker_timeout_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.failure_recency_seconds < 0:
            raise ValueError("failure_recency_seconds cannot be negative")
        if self.openai_api_key_env and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*",
            self.openai_api_key_env,
        ):
            raise ValueError("openai_api_key_env must be an environment name")
        seen_names: set[str] = set()
        for spec in self.custom_hooks:
            if not isinstance(spec, CustomHookSpec):
                raise TypeError(
                    "custom_hooks entries must be CustomHookSpec instances"
                )
            if spec.name in seen_names:
                raise ValueError(
                    f"duplicate custom_hook name {spec.name!r}; names must "
                    "be unique within a config"
                )
            seen_names.add(spec.name)
        seen_built_in: set[str] = set()
        for spec in self.built_in_hooks:
            if not isinstance(spec, BuiltInHookSpec):
                raise TypeError(
                    "built_in_hooks entries must be BuiltInHookSpec instances"
                )
            if spec.event in seen_built_in:
                raise ValueError(
                    f"duplicate built_in_hooks event {spec.event!r}"
                )
            seen_built_in.add(spec.event)

    def find_custom_hook(self, name: str) -> CustomHookSpec | None:
        for spec in self.custom_hooks:
            if spec.name == name:
                return spec
        return None

    @classmethod
    def load(cls, path: Path | str) -> "ClaudeCodeConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        scoped = data.get("claude_code") if isinstance(data.get("claude_code"), dict) else {}
        trace_output = str(data.get("trace_output", "")).strip()
        adamast_model = str(data.get("adamast_model", "")).strip()
        if not trace_output:
            raise ValueError("Claude Code integration requires trace_output")
        if not adamast_model:
            raise ValueError("Claude Code integration requires adamast_model")
        if data.get("openai_api_key"):
            raise ValueError(
                "plaintext openai_api_key is no longer supported; rerun "
                "adamast-claude-install with --openai-api-key-env"
            )
        inherit = data.get("inherit")
        if inherit in ("", "none"):
            inherit = None
        raw_hooks = scoped.get("custom_hooks", data.get("custom_hooks")) or ()
        if not isinstance(raw_hooks, list | tuple):
            raise ValueError("custom_hooks must be a list")
        custom_hooks = tuple(
            CustomHookSpec.from_dict(entry) for entry in raw_hooks
        )
        built_in_hooks = parse_built_in_hooks(
            scoped.get("built_in_hooks", data.get("built_in_hooks"))
        )
        return cls(
            trace_output=Path(trace_output).expanduser().resolve(),
            adamast_model=adamast_model,
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
            max_retries=max(0, int(data.get("max_retries", 3))),
            format_retries=max(1, int(data.get("format_retries", 2))),
            repair_rounds=(
                max(0, int(data["repair_rounds"]))
                if data.get("repair_rounds") is not None
                else None
            ),
            generation_threshold=max(
                1, int(data.get("generation_threshold", 5))
            ),
            generation_stops=bool(data.get("generation_stops", False)),
            skip_judge=bool(data.get("skip_judge", False)),
            k_init=max(1, int(data.get("k_init", 10))),
            k=max(1, int(data.get("k", 20))),
            refinement_stops=bool(data.get("refinement_stops", False)),
            advanced_refinement=bool(
                data.get("advanced_refinement", False)
            ),
            freeze=bool(data.get("freeze", False)),
            evidence_export=(
                Path(str(data["evidence_export"])).expanduser().resolve()
                if data.get("evidence_export")
                else None
            ),
            redact_traces=bool(data.get("redact_traces", True)),
            failure_throttle_calls=max(
                1, int(data.get("failure_throttle_calls", 5))
            ),
            failure_recency_seconds=max(
                0, int(data.get("failure_recency_seconds", 30))
            ),
            project_scope=str(scoped.get("project_scope", "explicit")),
            project_id=(
                str(scoped["project_id"]).strip()
                if scoped.get("project_id")
                else None
            ),
            task_group=str(scoped.get("task_group", "default")),
            session_selector=str(scoped.get("session_selector", "off")),
            selector_surface=str(scoped.get("selector_surface", "inline")),
            learning_backend=str(scoped.get("learning_backend", "provider")),
            worker_model=(
                str(scoped["worker_model"]).strip()
                if scoped.get("worker_model")
                else None
            ),
            claude_cli_path=(
                Path(str(scoped["claude_cli_path"])).expanduser().resolve()
                if scoped.get("claude_cli_path")
                else None
            ),
            worker_timeout_seconds=max(
                1, int(scoped.get("worker_timeout_seconds", 1800))
            ),
            built_in_hooks=built_in_hooks,
            custom_hooks=custom_hooks,
        )

    def for_event(self, event: dict) -> "ClaudeCodeConfig":
        """Return the project/group-scoped config for a user-level hook."""
        routing_root = self.routing_root or self.trace_output
        base_task_group = self.base_task_group or self.task_group
        routed_task_group = base_task_group
        if self.project_scope == "explicit":
            default_trace_output = self.default_trace_output or self.trace_output
        else:
            routed_task_group = host_task_group(
                base_task_group,
                host="claude_code",
            )
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
            trace_output=(route.trace_output if route else default_trace_output),
            task_group=route.task_group if route else routed_task_group,
            routing_root=routing_root,
            default_trace_output=default_trace_output,
            base_task_group=base_task_group,
        )

    def start_fresh_conversation(self, event: dict) -> "ClaudeCodeConfig":
        """Route this Claude conversation to a separate MAST task group."""
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
            "max_retries": self.repair_rounds,
            "format_retries": self.format_retries,
            "repair_rounds": self.repair_rounds,
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
            "failure_throttle_calls": self.failure_throttle_calls,
            "failure_recency_seconds": self.failure_recency_seconds,
            "claude_code": {
                "project_scope": self.project_scope,
                "project_id": self.project_id,
                "task_group": self.task_group,
                "session_selector": self.session_selector,
                "selector_surface": self.selector_surface,
                "learning_backend": self.learning_backend,
                "worker_model": self.worker_model,
                "claude_cli_path": (
                    str(self.claude_cli_path) if self.claude_cli_path else None
                ),
                "worker_timeout_seconds": self.worker_timeout_seconds,
                "built_in_hooks": {
                    spec.event: spec.to_dict() for spec in self.built_in_hooks
                },
                "custom_hooks": [spec.to_dict() for spec in self.custom_hooks],
            },
        }
