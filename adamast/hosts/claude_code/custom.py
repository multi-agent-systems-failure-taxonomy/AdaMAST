"""Reflection runtime for user-declared custom hooks.

``custom_blocking_checkpoint`` and ``custom_advisory`` mirror the
``blocking_checkpoint``/``post_tool`` pair in :mod:`runtime` but drop the
submission-gate logic (``READY_TO_SUBMIT``/``REPAIR_REQUIRED``), key state by
the in-flight tool-call id (so retries map back to the same pending block),
and use ``CustomHookSpec.name`` as the gate identifier. The point is that
any new Claude Code hook a user wants to opt into reuses the exact same
reflection<->refinement loop without new Python.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from adamast.core.evidence import record_reflection
from adamast.core.reflection import parse_reflection

from .config import ClaudeCodeConfig, CustomHookSpec
from .prompts import reflection_prompt
from .state import save_state
from .transcript import read_transcript, transcript_size


def custom_blocking_checkpoint(
    event: dict[str, Any],
    config: ClaudeCodeConfig,
    *,
    spec: CustomHookSpec,
) -> tuple[int, str]:
    """Block the hook until a valid AdaMAST reflection block appears in the
    transcript, then record it. No submission-gate counters.
    """
    from .runtime import (
        _checkpoint_id,
        _code_ids,
        _release_retry_guard,
        _retry_limit_reached,
        _state,
    )

    state = _state(event, config)
    if not _matches_command_pattern(event, spec):
        return 0, ""
    transcript_path = event.get("transcript_path")
    key = _custom_checkpoint_key(event, spec)
    pending = state.setdefault("pending", {}).get(key)

    if pending:
        recent = read_transcript(
            transcript_path, after=int(pending["offset"])
        )
        if event.get("last_assistant_message"):
            recent += "\n" + str(event["last_assistant_message"])
        try:
            reflection = parse_reflection(
                recent,
                checkpoint_id=pending["checkpoint_id"],
                known_code_ids=_code_ids(state),
            )
        except ValueError as exc:
            pending["guard_failures"] = int(
                pending.get("guard_failures", 0)
            ) + 1
            if _retry_limit_reached(pending, state):
                return _release_retry_guard(
                    config,
                    state,
                    key=key,
                    gate=f"custom:{spec.name}",
                    transcript_path=transcript_path,
                    detail=f"Last shape error: {exc}",
                )
            save_state(config.trace_output, state["session_id"], state)
            return 2, (
                f"AdaMAST reflection is incomplete: {exc}\n\n"
                + pending["prompt"]
            )

        if not pending.get("recorded"):
            record_reflection(
                config.trace_output,
                state,
                reflection,
                gate=f"custom:{spec.name}",
                task_id=key,
            )
            pending["recorded"] = True
        state["pending"].pop(key, None)
        save_state(config.trace_output, state["session_id"], state)
        return 0, "AdaMAST reflection accepted."

    checkpoint_id = _checkpoint_id(f"custom-{spec.name}")
    offset = transcript_size(transcript_path)
    recent = _recent_context(event, transcript_path, offset)
    prompt = reflection_prompt(
        state,
        checkpoint_id=checkpoint_id,
        gate_label=f"custom hook: {spec.name}",
        recent_activity=recent,
        full=False,
    )
    state["pending"][key] = {
        "checkpoint_id": checkpoint_id,
        "offset": offset,
        "prompt": prompt,
        "full": False,
        "guard_failures": 0,
        "repairs_completed": 0,
        "awaiting_repair": False,
        "recorded": False,
        "custom_hook": spec.name,
    }
    save_state(config.trace_output, state["session_id"], state)
    return 2, prompt


def custom_advisory(
    event: dict[str, Any],
    config: ClaudeCodeConfig,
    *,
    spec: CustomHookSpec,
) -> dict | None:
    """Emit a non-blocking nudge as ``additionalContext`` for this event.

    Stashes a pending entry so any reflection the assistant ends up writing
    can still be harvested by a later blocking gate via
    ``_harvest_advisory_reflections``.
    """
    from .runtime import _checkpoint_id, _context, _state

    if not _matches_command_pattern(event, spec):
        return None
    state = _state(event, config)
    checkpoint_id = _checkpoint_id(f"custom-{spec.name}")
    transcript_path = event.get("transcript_path")
    state.setdefault("pending", {})[f"nudge:{checkpoint_id}"] = {
        "checkpoint_id": checkpoint_id,
        "offset": transcript_size(transcript_path),
        "prompt": "",
        "full": False,
        "guard_failures": 0,
        "advisory": True,
        "recorded": False,
        "custom_hook": spec.name,
    }
    save_state(config.trace_output, state["session_id"], state)
    summary = _event_summary(event)
    nudge = reflection_prompt(
        state,
        checkpoint_id=checkpoint_id,
        gate_label=f"custom hook nudge: {spec.name} (advisory; non-blocking)",
        recent_activity=summary,
        full=False,
    )
    return _context(spec.event, nudge)


def _custom_checkpoint_key(
    event: dict[str, Any], spec: CustomHookSpec
) -> str:
    if spec.checkpoint_key == "fixed":
        return f"custom:{spec.name}:fixed"
    tool_use_id = str(event.get("tool_use_id") or "").strip()
    if spec.checkpoint_key == "tool_use_id" and tool_use_id:
        return f"custom:{spec.name}:{tool_use_id}"
    if spec.checkpoint_key == "command":
        payload = _tool_input_text(event) or _event_summary(event)
    else:
        payload = json.dumps(
            {
                "tool_name": event.get("tool_name"),
                "tool_input": event.get("tool_input"),
                "prompt": event.get("prompt"),
                "message": event.get("message"),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
    digest = hashlib.sha256(
        payload.encode("utf-8", "replace")
    ).hexdigest()[:12]
    return f"custom:{spec.name}:{digest}"


def _matches_command_pattern(event: dict[str, Any], spec: CustomHookSpec) -> bool:
    if not spec.command_pattern:
        return True
    text = _tool_input_text(event)
    if not text:
        return False
    return re.search(spec.command_pattern, text, re.S) is not None


def _tool_input_text(event: dict[str, Any]) -> str:
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        preferred = (
            tool_input.get("command")
            or tool_input.get("cmd")
            or tool_input.get("input")
            or tool_input.get("text")
        )
        if preferred is not None:
            return str(preferred)
    if isinstance(tool_input, str):
        return tool_input
    payload = json.dumps(
        tool_input if tool_input is not None else {},
        sort_keys=True,
        ensure_ascii=False,
    )
    return payload


def _recent_context(
    event: dict[str, Any], transcript_path: str | None, offset: int
) -> str:
    look_back = max(0, int(offset) - 12000)
    text = read_transcript(transcript_path, after=look_back)
    inline = _event_summary(event)
    if inline and inline not in text:
        text = (text + "\n" if text else "") + inline
    return text


def _event_summary(event: dict[str, Any]) -> str:
    keys = (
        "hook_event_name", "tool_name", "tool_input", "tool_response",
        "error", "prompt", "message",
    )
    surface = {k: event[k] for k in keys if k in event and event[k] is not None}
    if not surface:
        return ""
    try:
        return json.dumps(surface, ensure_ascii=False, indent=2)[:4000]
    except (TypeError, ValueError):
        return str(surface)[:4000]
