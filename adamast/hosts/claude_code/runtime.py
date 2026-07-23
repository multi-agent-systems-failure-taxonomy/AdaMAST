"""Shared Claude Code hook behavior."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adamast import (
    GenerationTrace,
    ProgramWorkspace,
    Session,
    SessionDelivery,
    end_session,
    redact_trace,
    start_session,
)
from adamast.core.evidence import annotate_checkpoints, record_reflection
from adamast.core.reflection import (
    parse_reflection,
)
from adamast.core import mast, resolver

from adamast.hosts.shared import build_session_state
from adamast.hosts.interactive.selector import (
    SELECTOR_VERSION,
    build_selection,
    parse_selection_choice,
    render_active_selection_context,
    render_selection,
    selection_interstitial,
)
from adamast.hosts.interactive.source import (
    conversation_source,
    require_compatible_taxonomy,
    stamp_program_source,
)

from .browser_picker import (
    allowed_option,
    apply_browser_choice,
    open_browser_picker,
    picker_alive,
    read_browser_choice,
    start_browser_picker,
    wait_for_browser_choice,
)
from .config import ClaudeCodeConfig
from .learning_jobs import (
    LearningJobError,
    capture_learning_receipt,
    enqueue_claude_learning_job,
)
from .prompts import (
    STANDING_PROMPT,
    checkpoint_transport_prompt,
    failure_nudge,
)
from .state import load_state, save_state
from .transcript import (
    first_user_message,
    read_raw_transcript,
    read_transcript,
    resolve_conversation_title,
    transcript_size,
    user_messages,
)

CHECKPOINT_REQUEST = re.compile(
    r"AdaMAST\s+checkpoint\s+request\s*:\s*(.+)",
    re.IGNORECASE,
)
FAILURE_PATTERNS = (
    re.compile(r"\bTraceback \(most recent call last\)", re.I),
    re.compile(r"\bAssertionError\b", re.I),
    re.compile(r"\b(?:FAILED|FAILURES?)\b", re.I),
    re.compile(r"\b(?:error|exception)\s*:", re.I),
    re.compile(r"\b(?:exit|return)\s+code\s*[:=]?\s*[1-9]\d*", re.I),
    re.compile(r"\bModuleNotFoundError\b|\bImportError\b|\bSyntaxError\b", re.I),
)


def _new_selector_state(
    event: dict[str, Any], config: ClaudeCodeConfig
) -> dict[str, Any]:
    session_id = _required(event, "session_id")
    title = resolve_conversation_title(session_id, event=event)
    source = conversation_source("claude_code", event, title=title)
    stamp_program_source(
        ProgramWorkspace(config.trace_output, repo_path=event.get("cwd")),
        source,
        store_dir=config.store_dir,
    )
    return {
        "version": 1,
        "session_id": session_id,
        "conversation_id": session_id,
        "cwd": str(event.get("cwd") or ""),
        "transcript_path": str(event.get("transcript_path") or ""),
        "conversation_host": "claude_code",
        "task_group": config.task_group,
        "conversation_title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "episode_sequence": 0,
        "main_cursor": transcript_size(event.get("transcript_path")),
        "episode_cursor": transcript_size(event.get("transcript_path")),
        "selection": build_selection(
            trace_output=config.trace_output,
            store_dir=config.store_dir,
            cwd=event.get("cwd"),
            catalog_mode=config.selector_surface,
            host="claude_code",
            source=source,
        ),
        "finished": True,
        "trace_captured": False,
    }


def session_start(event: dict[str, Any], config: ClaudeCodeConfig) -> dict:
    session_id = _required(event, "session_id")
    existing = load_state(config.trace_output, session_id)
    if existing and _refresh_conversation_metadata(existing, event, config):
        save_state(config.trace_output, session_id, existing)
    if config.session_selector == "prompt" and config.inherit is None:
        selection = existing.get("selection") if existing else None
        if selection:
            status = selection.get("status")
            if status == "pending":
                recovered_selection = _recover_pending_transcript_selection(
                    existing,
                    event,
                    config,
                )
                if recovered_selection:
                    existing = recovered_selection
                    selection = existing["selection"]
                    if selection.get("status") == "disabled":
                        return {
                            "systemMessage": (
                                "AdaMAST recovered the conversation's previous "
                                "No taxonomy choice."
                            )
                        }
                    return _context_with_message(
                        "SessionStart",
                        _selected_context(
                            selection,
                            state=existing,
                            config=config,
                        )
                        + "\n\n"
                        + _standing_context(existing, config),
                        "AdaMAST recovered the conversation's previous "
                        "taxonomy choice.",
                    )
                selection = _refresh_pending_selection(existing, event, config)
                save_state(config.trace_output, session_id, existing)
                if config.selector_surface == "inline":
                    return _selection_output("SessionStart", selection)
                return None
            if status == "browser_pending":
                return _browser_waiting_output(selection, "SessionStart")
            if status == "disabled":
                return {"systemMessage": "AdaMAST is disabled for this conversation."}
            return _context(
                "SessionStart",
                _selected_context(selection, state=existing, config=config)
                + "\n\n"
                + _standing_context(existing, config),
            )
        if not existing:
            # Claude Code also emits SessionStart for short-lived internal
            # agent sessions. Opening the browser here makes those invisible
            # host tasks look like new user conversations. Browser selection
            # starts on the first real UserPromptSubmit instead.
            if config.selector_surface == "browser":
                return None
            state = _new_selector_state(event, config)
            selection = state["selection"]
            save_state(config.trace_output, session_id, state)
            return _selection_output("SessionStart", selection)
    if existing and not existing.get("finished"):
        return _context("SessionStart", _standing_context(existing, config))

    sequence = int(existing.get("episode_sequence", 0)) + 1 if existing else 1
    state, session = _start_episode(
        event,
        config,
        sequence=sequence,
        cursor=transcript_size(event.get("transcript_path")),
        previous=existing,
    )
    save_state(config.trace_output, session_id, state)
    context = _standing_context(state, config)
    return _context("SessionStart", context)


def _start_episode(
    event: dict[str, Any],
    config: ClaudeCodeConfig,
    *,
    sequence: int,
    cursor: int,
    previous: dict[str, Any] | None = None,
    taxonomy_id: str | None = None,
    episode_task: str | None = None,
) -> tuple[dict[str, Any], Session]:
    """Start one runtime task inside a longer Claude conversation."""
    session_id = _required(event, "session_id")
    title = resolve_conversation_title(
        session_id,
        event=event,
        existing=(previous or {}).get("conversation_title"),
        prompt=episode_task,
    )
    source = conversation_source(
        "claude_code",
        event,
        title=title,
        prompt=episode_task,
    )
    stamp_program_source(
        ProgramWorkspace(config.trace_output, repo_path=event.get("cwd")),
        source,
        store_dir=config.store_dir,
    )

    manifest_path = Path(config.trace_output) / ".adamast-program.json"
    bound_taxonomy_id = None
    if manifest_path.exists():
        try:
            bound_taxonomy_id = json.loads(
                manifest_path.read_text(encoding="utf-8")
            ).get("taxonomy_id")
        except (OSError, json.JSONDecodeError):
            bound_taxonomy_id = None
    if bound_taxonomy_id:
        taxonomy_id = str(bound_taxonomy_id)
    require_compatible_taxonomy(
        taxonomy_id,
        host="claude_code",
        store_dir=config.store_dir,
    )

    if taxonomy_id == mast.MAST_ID:
        inherit = resolver.ABSENT
    elif taxonomy_id:
        inherit = taxonomy_id
    else:
        inherit = config.inherit if config.inherit is not None else resolver.ABSENT
    session = start_session(
        inherit,
        trace_output=config.trace_output,
        store_dir=config.store_dir,
        trace_root=config.trace_root,
        session_id=f"claude-code:{session_id}:episode:{sequence}",
        adamast_model=config.adamast_model,
        repo_path=event.get("cwd") or Path.cwd(),
        max_retries=config.repair_rounds,
        dashboard=False,
        generation_threshold=config.generation_threshold,
        # Hook processes are killed at Claude Code's per-hook timeout, so
        # learning always runs in background workers here; the *_stops flags
        # only make sense for CLI/benchmark wrappers that own their process.
        generation_stops=False,
        skip_judge=config.skip_judge,
        k_init=config.k_init,
        k=config.k,
        refinement_stops=False,
        advanced_refinement=config.advanced_refinement,
        freeze=config.freeze,
        evidence_export=config.evidence_export,
    )
    if config.dashboard:
        try:
            from adamast.dashboard.server import ensure_dashboard

            dashboard_url = ensure_dashboard(
                session.workspace,
                config.store_dir,
                monitor_root=config.routing_root or config.trace_output,
                project_id=_monitor_project_id(config),
                conversation_id=session_id,
            )
            if dashboard_url:
                session.delivery = SessionDelivery(
                    taxonomy_id=session.delivery.taxonomy_id,
                    taxonomy=session.delivery.taxonomy,
                    runtime_protocol=session.delivery.runtime_protocol,
                    dashboard_url=dashboard_url,
                )
        except Exception:
            pass
    state = build_session_state(
        session_id=session_id,
        session=session,
        cwd=str(event.get("cwd") or ""),
        max_retries=config.repair_rounds,
        main_cursor=cursor,
        episode_sequence=sequence,
        episode_cursor=cursor,
        failure={
            "call_index": 0,
            "last_fired_call": -10**9,
            "last_hash": "",
            "last_fired_at": 0.0,
        },
    )
    state["format_retries"] = config.format_retries
    state["repair_rounds"] = config.repair_rounds
    state["conversation_id"] = session_id
    state["conversation_host"] = "claude_code"
    state["task_group"] = config.task_group
    state["transcript_path"] = str(event.get("transcript_path") or "")
    state["created_at"] = (previous or {}).get("created_at") or datetime.now(
        timezone.utc
    ).isoformat()
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["conversation_title"] = title
    state["monitor_opened"] = bool((previous or {}).get("monitor_opened"))
    if session.delivery.dashboard_url and not state["monitor_opened"]:
        state["monitor_opened"] = bool(webbrowser.open(session.delivery.dashboard_url))
    state["conversation_taxonomy_root"] = (
        (previous or {}).get("conversation_taxonomy_root")
        or session.delivery.taxonomy_id
    )
    if previous:
        state["previous_taxonomy_id"] = previous.get("taxonomy_id")
        if previous.get("selection"):
            state["selection"] = previous["selection"]
    if episode_task:
        state["episode_task"] = episode_task
    return state, session


def user_prompt_submit(
    event: dict[str, Any], config: ClaudeCodeConfig
) -> dict | None:
    """Resolve the session selector and preserve the held substantive prompt."""
    session_id = _required(event, "session_id")
    state = load_state(config.trace_output, session_id)
    if not state:
        session_start({**event, "hook_event_name": "SessionStart"}, config)
        state = load_state(config.trace_output, session_id)
    if not state and config.selector_surface == "browser":
        state = _new_selector_state(event, config)
        save_state(config.trace_output, session_id, state)
    prompt = _user_prompt(event)
    if config.session_selector != "prompt" or config.inherit is not None:
        if not state or not prompt:
            return None
        if state.get("finished"):
            fresh, _session = _start_episode(
                event,
                config,
                sequence=max(1, int(state.get("episode_sequence", 0)) + 1),
                cursor=transcript_size(event.get("transcript_path")),
                previous=state,
                episode_task=prompt,
            )
            save_state(config.trace_output, session_id, fresh)
        elif not state.get("episode_task"):
            state["episode_task"] = prompt
            if not state.get("conversation_title") or str(
                state.get("conversation_title")
            ).startswith("Conversation "):
                state["conversation_title"] = prompt
            save_state(config.trace_output, session_id, state)
        return None

    selection = state.get("selection")
    if not selection:
        return None

    if (
        selection.get("status") == "pending"
        and int(selection.get("version") or 0) < SELECTOR_VERSION
    ):
        selection = _refresh_pending_selection(state, event, config)
        save_state(config.trace_output, session_id, state)
        return _selection_output("UserPromptSubmit", selection)

    status = selection.get("status")
    if status == "disabled":
        return None
    if status in {"pending", "browser_pending"}:
        choice = None
        if status == "browser_pending":
            waited_for_picker = False
            taxonomy_id = read_browser_choice(
                selection.get("browser_picker"),
                store_dir=config.store_dir,
            )
            if not taxonomy_id and picker_alive(selection.get("browser_picker")):
                waited_for_picker = True
                taxonomy_id = wait_for_browser_choice(
                    selection.get("browser_picker"),
                    store_dir=config.store_dir,
                    timeout_seconds=config.worker_timeout_seconds,
                )
            if taxonomy_id:
                choice = allowed_option(
                    selection,
                    taxonomy_id,
                    config.store_dir,
                )
                selection["status"] = "pending"
                if (
                    prompt
                    and not selection.get("pending_task")
                    and not _browser_continuation_prompt(prompt)
                ):
                    selection["pending_task"] = prompt
            else:
                if (
                    prompt
                    and not selection.get("pending_task")
                    and not _browser_continuation_prompt(prompt)
                ):
                    selection["pending_task"] = prompt
                if waited_for_picker:
                    save_state(config.trace_output, session_id, state)
                    return _browser_timeout_output(
                        selection,
                        "UserPromptSubmit",
                    )
                if not picker_alive(selection.get("browser_picker")):
                    # The detached picker worker timed out or died; relaunch
                    # it so the conversation cannot wait on a dead page.
                    return _launch_selection_browser(
                        state,
                        event,
                        config,
                        event_name="UserPromptSubmit",
                    )
                save_state(config.trace_output, session_id, state)
                return _browser_timeout_output(selection, "UserPromptSubmit")

        if choice is None:
            choice = parse_selection_choice(prompt, selection)
        if choice is None:
            if prompt and not selection.get("pending_task"):
                selection["pending_task"] = prompt
                selection["held_cursor"] = transcript_size(
                    event.get("transcript_path")
                )
                save_state(config.trace_output, session_id, state)
            if config.selector_surface == "browser" and prompt:
                return _launch_selection_browser(
                    state,
                    event,
                    config,
                    event_name="UserPromptSubmit",
                )
            return _selection_block(selection)

        if choice["kind"] == "browser":
            return _launch_selection_browser(
                state,
                event,
                config,
                event_name="UserPromptSubmit",
            )

        return _accept_selection_choice(
            state,
            event,
            config,
            choice,
        )

    if status == "selected" and (not state.get("lifecycle") or state.get("finished")):
        if not prompt:
            return None
        fresh, _session = _start_episode(
            event,
            config,
            sequence=max(1, int(state.get("episode_sequence", 0)) + 1),
            cursor=transcript_size(event.get("transcript_path")),
            previous=state,
            taxonomy_id=str(selection["selected_taxonomy_id"]),
            episode_task=prompt,
        )
        save_state(config.trace_output, session_id, fresh)
        return _context(
            "UserPromptSubmit",
            _selected_context(selection, state=fresh, config=config)
            + "\n\n"
            + _standing_context(fresh, config),
        )
    return None


def _accept_selection_choice(
    state: dict[str, Any],
    event: dict[str, Any],
    config: ClaudeCodeConfig,
    choice: dict[str, Any],
    *,
    original_prompt_continues: bool = False,
) -> dict:
    """Activate one selector choice while preserving the held first prompt."""
    session_id = _required(event, "session_id")
    selection = state.get("selection") or {}
    selection["selected_kind"] = choice["kind"]
    selection["selected_taxonomy_id"] = choice.get("taxonomy_id")
    selection["selected_label"] = choice["label"]
    pending_task = str(selection.get("pending_task") or "").strip()
    if choice["kind"] == "disabled":
        selection["status"] = "disabled"
        state["finished"] = True
        save_state(config.trace_output, session_id, state)
        return _context_with_message(
            "UserPromptSubmit",
            _disabled_context(
                pending_task,
                original_prompt_continues=original_prompt_continues,
            ),
            "AdaMAST disabled for this conversation.",
        )

    target_config = config
    if choice.get("starts_fresh"):
        target_config = config.start_fresh_conversation(event)
        selection["fresh_task_group"] = target_config.task_group
        selection["shared_taxonomy_preserved"] = selection.get(
            "project_taxonomy_id"
        )
        if target_config.trace_output != config.trace_output:
            routed_state = dict(state)
            routed_state["selection"] = dict(selection)
            source_state = dict(state)
            source_state["selection"] = {**selection, "status": "routed"}
            save_state(config.trace_output, session_id, source_state)
            state = routed_state

    selection["status"] = "selected"
    state["selection"] = selection
    if pending_task:
        fresh, _session = _start_episode(
            event,
            target_config,
            sequence=max(1, int(state.get("episode_sequence", 0)) + 1),
            cursor=transcript_size(event.get("transcript_path")),
            previous=state,
            taxonomy_id=str(choice["taxonomy_id"]),
            episode_task=pending_task,
        )
        save_state(target_config.trace_output, session_id, fresh)
    else:
        save_state(target_config.trace_output, session_id, state)
    return _context_with_message(
        "UserPromptSubmit",
        _selection_accepted_context(
            selection,
            pending_task,
            state=(fresh if pending_task else state),
            config=target_config,
            original_prompt_continues=original_prompt_continues,
        ),
        f"AdaMAST selected {choice['label']}.",
    )


def _ensure_active_episode(
    event: dict[str, Any],
    config: ClaudeCodeConfig,
    state: dict[str, Any],
) -> dict[str, Any]:
    selection = state.get("selection") or {}
    if selection.get("status") in {"pending", "disabled"}:
        return state
    if not state.get("finished"):
        return state
    sequence = int(state.get("episode_sequence", 0)) + 1
    cursor = int(
        state.get("episode_cursor", state.get("main_cursor", 0))
    )
    if not read_raw_transcript(
        event.get("transcript_path"),
        after=cursor,
    ).strip():
        return state
    fresh, _session = _start_episode(
        event,
        config,
        sequence=sequence,
        cursor=cursor,
        previous=state,
        taxonomy_id=selection.get("selected_taxonomy_id"),
    )
    save_state(config.trace_output, fresh["session_id"], fresh)
    return fresh


def blocking_checkpoint(
    event: dict[str, Any],
    config: ClaudeCodeConfig,
    *,
    gate: str,
) -> tuple[int, str]:
    state = _ensure_active_episode(event, config, _state(event, config))
    if _selection_inactive(state):
        return 0, "AdaMAST taxonomy selection is pending or disabled."
    if state.get("finished"):
        return 0, "AdaMAST episode already committed."
    transcript_path = _transcript_path(event, gate)
    _harvest_advisory_reflections(
        state,
        config,
        transcript_path=event.get("transcript_path"),
    )
    direct = _latest_recorded_checkpoint(state, gate)
    if direct:
        status = str(direct.get("status") or "READY_TO_SUBMIT")
        checkpoint_ids = [
            str(item.get("checkpoint_id"))
            for item in state.get("recorded_checkpoints", [])
            if isinstance(item, dict)
            and int(item.get("episode_sequence", -1))
            == int(state.get("episode_sequence", 1))
            and item.get("checkpoint_id")
        ]
        annotate_checkpoints(
            config.trace_output,
            checkpoint_ids,
            turn_id=(str(event["prompt_id"]) if event.get("prompt_id") else None),
            gate_status=status,
        )
        state["gate_result"] = {
            "status": status,
            "error": direct.get("error"),
            "turn_id": event.get("prompt_id"),
            "checkpoint_id": direct.get("checkpoint_id"),
        }
        direct["accepted_at"] = time.time()
        direct["accepted_prompt_id"] = event.get("prompt_id")
        if status == "REPAIR_REQUIRED":
            save_state(config.trace_output, state["session_id"], state)
            return 2, (
                "AdaMAST recorded a repair-required gate. Complete the recorded "
                "next action, then replace it with a ready checkpoint."
            )
        if gate == "stop":
            _finish_runtime_session(
                state,
                config,
                transcript_path=transcript_path,
                reason="stop_gate",
            )
        state.setdefault("pending", {}).pop(_checkpoint_key(event, gate), None)
        if gate != "subagent_stop":
            state["main_cursor"] = transcript_size(transcript_path)
        save_state(config.trace_output, state["session_id"], state)
        return 0, ""

    # Private transport is the only supported checkpoint source. Claude Code
    # may display a blocking hook reason, so keep this retry operational and
    # never include taxonomy codes, evidence, or reflection instructions.
    state["gate_result"] = {
        "status": "MISSING_PRIVATE_CHECKPOINT",
        "error": "private recorder was not called before the gate",
        "turn_id": event.get("prompt_id"),
        "checkpoint_id": None,
    }
    save_state(config.trace_output, state["session_id"], state)
    return 2, (
        "AdaMAST needs its private checkpoint before this gate can finish. "
        "Keep it out of chat and retry the gate after running: "
        "adamast-claude-checkpoint --trace-output "
        f'"{Path(config.trace_output).expanduser().resolve()}" --session-id '
        f'"{state["session_id"]}" --gate "{gate}" with the four compact '
        "field flags."
    )
def _latest_recorded_checkpoint(
    state: dict[str, Any],
    gate: str,
) -> dict[str, Any] | None:
    episode_sequence = int(state.get("episode_sequence", 1))
    return next(
        (
            item
            for item in reversed(state.get("recorded_checkpoints", []))
            if isinstance(item, dict)
            and item.get("gate") == gate
            and not item.get("accepted_at")
            and int(item.get("episode_sequence", -1)) == episode_sequence
        ),
        None,
    )


def subagent_stop(
    event: dict[str, Any],
    config: ClaudeCodeConfig,
) -> tuple[int, dict | str]:
    """Accept taxonomy receipts without recursively gating the worker."""
    workspace = ProgramWorkspace(config.trace_output, repo_path=event.get("cwd"))
    try:
        learning_job_id = capture_learning_receipt(workspace, event)
    except (LearningJobError, OSError, ValueError) as exc:
        return 0, {
            "systemMessage": f"AdaMAST ignored an invalid taxonomy receipt: {exc}"
        }
    if learning_job_id:
        return 0, _context_with_message(
            "SubagentStop",
            (
                f"AdaMAST taxonomy proposal received for {learning_job_id}; "
                "normal hook reconciliation will validate it."
            ),
            f"AdaMAST taxonomy proposal received for {learning_job_id}.",
        )
    return blocking_checkpoint(event, config, gate="subagent_stop")


def post_tool(
    event: dict[str, Any],
    config: ClaudeCodeConfig,
    *,
    execution_failed: bool,
) -> dict | None:
    state = _ensure_active_episode(event, config, _state(event, config))
    if _selection_inactive(state):
        return None
    if state.get("finished"):
        return None
    failure = state.setdefault("failure", {})
    failure["call_index"] = int(failure.get("call_index", 0)) + 1
    text = (
        str(event.get("error", ""))
        if execution_failed
        else json.dumps(event.get("tool_response", ""), ensure_ascii=False)
    )
    if not execution_failed and not any(p.search(text) for p in FAILURE_PATTERNS):
        save_state(config.trace_output, state["session_id"], state)
        return None

    digest = hashlib.sha256(text[:8000].encode("utf-8", "replace")).hexdigest()[:16]
    now = time.time()
    throttled = (
        failure.get("last_hash") == digest
        or int(failure["call_index"]) - int(
            failure.get("last_fired_call", -10**9)
        ) < config.failure_throttle_calls
        or now - float(failure.get("last_fired_at", 0))
        < config.failure_recency_seconds
    )
    if throttled:
        save_state(config.trace_output, state["session_id"], state)
        return None

    failure.update(
        {
            "last_hash": digest,
            "last_fired_call": failure["call_index"],
            "last_fired_at": now,
        }
    )
    checkpoint_id = _checkpoint_id("failure")
    transcript_path = event.get("transcript_path")
    state.setdefault("pending", {})[f"nudge:{checkpoint_id}"] = {
        "checkpoint_id": checkpoint_id,
        "offset": transcript_size(transcript_path),
        "prompt": "",
        "full": False,
        "guard_failures": 0,
        "advisory": True,
        "recorded": False,
    }
    save_state(config.trace_output, state["session_id"], state)
    return _context(
        "PostToolUseFailure" if execution_failed else "PostToolUse",
        failure_nudge(
            state,
            checkpoint_id=checkpoint_id,
            failure_summary=text[-4000:],
        ),
    )


def _harvest_advisory_reflections(
    state: dict[str, Any],
    config: ClaudeCodeConfig,
    *,
    transcript_path: str | None,
) -> None:
    """Persist completed nonblocking nudge reflections when a later hook fires."""
    changed = False
    for key, pending in list(state.setdefault("pending", {}).items()):
        if not pending.get("advisory"):
            continue
        recent = read_transcript(
            transcript_path, after=int(pending.get("offset", 0))
        )
        try:
            reflection = parse_reflection(
                recent,
                checkpoint_id=pending["checkpoint_id"],
                known_code_ids=_code_ids(state),
            )
        except ValueError:
            continue
        record_reflection(
            config.trace_output,
            state,
            reflection,
            gate="post_tool_failure",
            task_id=str(state["session_id"]),
        )
        state["pending"].pop(key, None)
        state["main_cursor"] = transcript_size(transcript_path)
        changed = True
    if changed:
        save_state(config.trace_output, state["session_id"], state)


def _state(
    event: dict[str, Any], config: ClaudeCodeConfig
) -> dict[str, Any]:
    session_id = _required(event, "session_id")
    state = load_state(config.trace_output, session_id)
    if not state:
        session_start(event, config)
        state = load_state(config.trace_output, session_id)
    if state and _refresh_conversation_metadata(state, event, config):
        save_state(config.trace_output, session_id, state)
    if (state.get("selection") or {}).get("status") == "pending":
        state = (
            _recover_pending_transcript_selection(state, event, config)
            or state
        )
    runtime_session_id = state.get("runtime_session_id")
    if runtime_session_id and not state.get("finished"):
        workspace = ProgramWorkspace(config.trace_output)
        workspace.heartbeat_session(str(runtime_session_id))
        workspace.reconcile_stale_sessions()
    return state


def _recover_pending_transcript_selection(
    state: dict[str, Any],
    event: dict[str, Any],
    config: ClaudeCodeConfig,
) -> dict[str, Any] | None:
    """Migrate an exact inline choice missed by a Claude hook."""
    selection = state.get("selection") or {}
    if selection.get("status") != "pending":
        return None
    cursor = int(
        selection.get("held_cursor")
        or state.get("main_cursor")
        or state.get("episode_cursor")
        or 0
    )
    choice = None
    for prompt in user_messages(event.get("transcript_path"), after=cursor):
        candidate = parse_selection_choice(prompt, selection)
        if candidate and candidate.get("kind") != "browser":
            choice = candidate
            break
    if choice is None or choice.get("starts_fresh"):
        return None

    choice_value = (
        "none"
        if choice.get("kind") == "disabled"
        else str(choice.get("taxonomy_id") or "")
    )
    receipt = apply_browser_choice(
        {
            "version": 1,
            "session_id": _required(event, "session_id"),
            "trace_output": str(config.trace_output),
            "store_dir": str(config.store_dir),
            "selection": selection,
            "event": {
                "cwd": event.get("cwd"),
                "session_id": _required(event, "session_id"),
            },
            "routing_root": str(config.routing_root or config.trace_output),
            "default_trace_output": str(
                config.default_trace_output or config.trace_output
            ),
            "task_group": config.task_group,
            "project_scope": config.project_scope,
            "project_id": config.project_id,
        },
        choice_value,
    )
    recovered = load_state(
        Path(receipt["trace_output"]),
        _required(event, "session_id"),
    )
    if not recovered:
        return None
    current_size = transcript_size(event.get("transcript_path"))
    recovered["main_cursor"] = current_size
    recovered["episode_cursor"] = current_size
    recovered["selector_recovery"] = {
        "source": "transcript",
        "choice": choice_value,
        "recovered_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(
        Path(receipt["trace_output"]),
        _required(event, "session_id"),
        recovered,
    )
    return recovered


def _finish_runtime_session(
    state: dict[str, Any],
    config: ClaudeCodeConfig,
    *,
    transcript_path: str | None,
    reason: str,
) -> None:
    if state.get("finished") and state.get("trace_captured"):
        return
    lifecycle = state["lifecycle"]
    workspace = ProgramWorkspace(config.trace_output)
    delivery = SessionDelivery(
        taxonomy_id=str(state["taxonomy_id"]),
        taxonomy=state["taxonomy"],
        runtime_protocol=str(lifecycle["runtime_protocol"]),
        dashboard_url=state.get("dashboard_url"),
    )
    session = Session(
        session_id=str(state["runtime_session_id"]),
        program_id=str(state["program_id"]),
        workspace=workspace,
        delivery=delivery,
        store_dir=Path(lifecycle["store_dir"]),
        trace_root=Path(lifecycle["trace_root"]),
        max_retries=int(state["max_retries"]),
        generation_threshold=int(lifecycle["generation_threshold"]),
        # Hooks run under Claude Code's per-hook timeout: learning must never
        # run inline here or the kill lands mid-finalize. Background workers
        # only, regardless of the configured *_stops flags.
        generation_stops=False,
        adamast_model=config.adamast_model,
        skip_judge=bool(lifecycle.get("skip_judge", False)),
        k_init=int(lifecycle["k_init"]),
        k=int(lifecycle["k"]),
        refinement_stops=False,
        advanced_refinement=bool(lifecycle["advanced_refinement"]),
        freeze=bool(lifecycle.get("freeze", False)),
        evidence_export=(
            Path(lifecycle["evidence_export"])
            if lifecycle.get("evidence_export")
            else None
        ),
    )
    cursor = int(state.get("episode_cursor", 0))
    sequence = int(state.get("episode_sequence", 1))
    persisted_trace_names = [
        str(name) for name in state.get("persisted_trace_names", [])
    ]
    raw_trajectory = ""
    if not persisted_trace_names:
        raw_trajectory = read_raw_transcript(
            transcript_path,
            after=cursor,
        ).strip()
    if not persisted_trace_names and raw_trajectory:
        task = str(state.get("episode_task") or "").strip() or first_user_message(
            transcript_path,
            after=cursor,
        ).strip() or (
            f"Claude Code episode {sequence} in "
            f"{state.get('cwd') or 'unknown working directory'}"
        )
        trace = GenerationTrace(
            problem_id=(
                f"claude-code:{state['session_id']}:episode:{sequence}"
            ),
            task=task,
            raw_trajectory=raw_trajectory,
            metadata={
                "harness": "claude_code",
                "claude_session_id": state["session_id"],
                "conversation_id": state["session_id"],
                "episode_sequence": sequence,
                "trace_granularity": "episode",
                "runtime_session_id": state["runtime_session_id"],
                "taxonomy_id": state["taxonomy_id"],
                "end_reason": reason,
            },
        )
        if config.redact_traces:
            trace = redact_trace(trace)
        persisted_trace_names = workspace.pending.append_many_with_names([trace])
        state["trace_captured"] = True
        state["persisted_trace_names"] = persisted_trace_names
        save_state(config.trace_output, state["session_id"], state)
    generation_launcher = None
    refinement_launcher = None
    if config.learning_backend == "claude_subagent":
        common = {
            "store_dir": config.store_dir,
            "trace_root": config.trace_root,
            "task_group": config.task_group,
            "conversation_id": str(state["session_id"]),
            "worker_model": config.worker_model,
            "claude_cli_path": config.claude_cli_path,
            "worker_timeout_seconds": config.worker_timeout_seconds,
        }
        def generation_launcher():
            return enqueue_claude_learning_job(
                workspace,
                kind="generation",
                **common,
            )

        def refinement_launcher():
            return enqueue_claude_learning_job(
                workspace,
                kind="refinement",
                **common,
            )
    result = end_session(
        session,
        background_launcher=generation_launcher,
        refinement_background_launcher=refinement_launcher,
        pre_persisted_trace_names=persisted_trace_names,
    )
    state["trace_captured"] = bool(persisted_trace_names)
    state["trace_capture"] = {
        "persisted_traces": result.persisted_traces,
        "integrated_traces": result.integrated_traces,
        "generation_action": result.generation.action,
        "refinement_action": result.refinement.action,
        "reason": reason,
    }
    state.pop("persisted_trace_names", None)
    state["episode_cursor"] = transcript_size(transcript_path)
    state["finished"] = True


def session_end(
    event: dict[str, Any], config: ClaudeCodeConfig
) -> tuple[int, str | None]:
    """Capture and close sessions that terminate without a successful Stop gate."""
    state = _state(event, config)
    if _selection_inactive(state):
        return 0, None
    if state.get("finished"):
        return 0, None
    _harvest_advisory_reflections(
        state,
        config,
        transcript_path=event.get("transcript_path"),
    )
    _finish_runtime_session(
        state,
        config,
        transcript_path=event.get("transcript_path"),
        reason=f"session_end:{event.get('reason') or 'unknown'}",
    )
    save_state(config.trace_output, state["session_id"], state)
    return 0, "AdaMAST captured the Claude Code session trace."


def _format_retries(state: dict[str, Any]) -> int:
    """Compatibility budget used by explicitly configured custom hooks."""
    return max(1, int(state.get("format_retries", 2)))


def _retry_limit_reached(
    pending: dict[str, Any], state: dict[str, Any]
) -> bool:
    return int(pending.get("guard_failures", 0)) >= _format_retries(state)


def _log_decision(config: ClaudeCodeConfig, payload: dict[str, Any]) -> None:
    """Append one custom-hook audit record to ``decisions.log``."""
    try:
        decisions_log = Path(config.trace_output) / "decisions.log"
        decisions_log.parent.mkdir(parents=True, exist_ok=True)
        with decisions_log.open("a", encoding="utf-8") as handle:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            handle.write(json.dumps({"ts": timestamp, **payload}) + "\n")
    except OSError:
        pass


def _release_retry_guard(
    config: ClaudeCodeConfig,
    state: dict[str, Any],
    *,
    key: str,
    gate: str,
    transcript_path: str | None,
    detail: str,
) -> tuple[int, str]:
    """Release an opt-in custom hook after its format retry budget."""
    pending = state.get("pending", {}).get(key, {}) or {}
    format_failures = int(pending.get("guard_failures", 0))
    format_retries = _format_retries(state)
    state["pending"].pop(key, None)
    save_state(config.trace_output, state["session_id"], state)
    summary = (
        f"[adamast] {gate} gate released after retry guard hit "
        f"(format_failures={format_failures}/{format_retries}). "
        f"detail={detail!r}"
    )
    print(summary, file=sys.stderr)
    _log_decision(
        config,
        {
            "event": "retry_guard_release",
            "gate": gate,
            "session_id": state.get("session_id"),
            "format_failures": format_failures,
            "format_retries": format_retries,
            "detail": detail,
        },
    )
    return (
        0,
        "AdaMAST released this custom boundary after its hook-owned retry "
        f"limit to prevent an infinite loop. {detail}",
    )


def _checkpoint_key(event: dict[str, Any], gate: str) -> str:
    if gate == "task_completed":
        return f"task:{event.get('task_id', 'unknown')}"
    if gate == "subagent_stop":
        return f"agent:{event.get('agent_id', 'unknown')}"
    return "stop"


def _checkpoint_id(gate: str) -> str:
    return f"{gate}-{uuid.uuid4().hex[:12]}"


def _task_id(event: dict[str, Any], gate: str) -> str:
    if gate == "task_completed":
        return str(event.get("task_id") or event["session_id"])
    if gate == "subagent_stop":
        return str(event.get("agent_id") or event["session_id"])
    return str(event["session_id"])


def _transcript_path(event: dict[str, Any], gate: str) -> str | None:
    if gate == "subagent_stop":
        return event.get("agent_transcript_path") or event.get("transcript_path")
    return event.get("transcript_path")


def _code_ids(state: dict[str, Any]) -> list[str]:
    return [str(code["id"]) for code in state["taxonomy"]["codes"]]


def _context(event_name: str, text: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }


def _context_with_message(event_name: str, text: str, message: str) -> dict:
    output = _context(event_name, text)
    output["systemMessage"] = message
    return output


def _selection_output(event_name: str, selection: dict[str, Any]) -> dict:
    return _context_with_message(
        event_name,
        selection_interstitial(selection),
        render_selection(selection),
    )


def _refresh_pending_selection(
    state: dict[str, Any],
    event: dict[str, Any],
    config: ClaudeCodeConfig,
) -> dict[str, Any]:
    current = state.get("selection") or {}
    if int(current.get("version") or 0) >= SELECTOR_VERSION:
        return current
    refreshed = build_selection(
        trace_output=config.trace_output,
        store_dir=config.store_dir,
        cwd=event.get("cwd"),
        catalog_mode=config.selector_surface,
        host="claude_code",
        source=conversation_source(
            "claude_code",
            event,
            title=state.get("conversation_title"),
            prompt=state.get("episode_task"),
        ),
    )
    for key in ("pending_task", "held_cursor"):
        if current.get(key) is not None:
            refreshed[key] = current[key]
    state["selection"] = refreshed
    return refreshed


def _launch_selection_browser(
    state: dict[str, Any],
    event: dict[str, Any],
    config: ClaudeCodeConfig,
    *,
    event_name: str,
) -> dict:
    selection = state.get("selection") or {}
    try:
        picker = start_browser_picker(
            config.trace_output,
            _required(event, "session_id"),
            store_dir=config.store_dir,
            selection=selection,
            event=event,
            routing_root=config.routing_root or config.trace_output,
            default_trace_output=(
                config.default_trace_output or config.trace_output
            ),
            task_group=config.task_group,
            project_scope=config.project_scope,
            project_id=config.project_id,
            timeout_seconds=config.worker_timeout_seconds,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        output = _context_with_message(
            event_name,
            "AdaMAST could not open its local taxonomy library. Do not perform "
            "the held task yet; report the selector error to the user.",
            f"AdaMAST could not open the taxonomy library: {exc}",
        )
        if event_name == "UserPromptSubmit":
            output.update(decision="block", reason=output["systemMessage"])
        return output
    selection["status"] = "browser_pending"
    selection["browser_picker"] = picker
    state["selection"] = selection
    save_state(config.trace_output, _required(event, "session_id"), state)
    open_browser_picker(picker)
    if event_name == "UserPromptSubmit":
        taxonomy_id = wait_for_browser_choice(
            picker,
            store_dir=config.store_dir,
            timeout_seconds=config.worker_timeout_seconds,
        )
        if taxonomy_id:
            choice = allowed_option(selection, taxonomy_id, config.store_dir)
            selection["status"] = "pending"
            return _accept_selection_choice(
                state,
                event,
                config,
                choice,
                original_prompt_continues=True,
            )
        return _browser_timeout_output(selection, event_name)
    return _browser_opened_output(selection, event_name)


def _browser_opened_output(
    selection: dict[str, Any],
    event_name: str,
) -> dict:
    picker = selection.get("browser_picker") or {}
    url = str(picker.get("url") or "the local AdaMAST catalog")
    output = _context_with_message(
        event_name,
        "AdaMAST taxonomy selection is waiting in the local browser. The catalog "
        f"opened at {url}. Do not perform the held task yet. Ask the user to "
        "choose a taxonomy there. The browser applies it directly to this "
        "conversation; return to Claude Code when activation is confirmed.",
        "AdaMAST taxonomy library opened in the browser.",
    )
    if event_name == "UserPromptSubmit":
        output.update(
            decision="block",
            reason=output["systemMessage"],
            suppressOriginalPrompt=True,
        )
    return output


def _browser_timeout_output(
    selection: dict[str, Any],
    event_name: str,
) -> dict:
    picker = selection.get("browser_picker") or {}
    url = str(picker.get("url") or "the local AdaMAST catalog")
    output = _context_with_message(
        event_name,
        "AdaMAST did not receive a taxonomy choice from the local browser at "
        f"{url} before the selector timeout. Do not perform the held task yet; "
        "ask the user to submit it again to reopen the taxonomy library.",
        "AdaMAST taxonomy selection timed out; submit the task again to reopen it.",
    )
    if event_name == "UserPromptSubmit":
        output.update(
            decision="block",
            reason=output["systemMessage"],
            suppressOriginalPrompt=True,
        )
    return output


def _browser_waiting_output(
    selection: dict[str, Any],
    event_name: str,
) -> dict:
    picker = selection.get("browser_picker") or {}
    url = str(picker.get("url") or "the local AdaMAST catalog")
    return _context_with_message(
        event_name,
        "AdaMAST taxonomy selection is still waiting in the local browser at "
        f"{url}. Finish the selection before submitting the next task.",
        "AdaMAST is waiting for the browser taxonomy selection.",
    )


def _selection_block(selection: dict[str, Any]) -> dict:
    output = _selection_output("UserPromptSubmit", selection)
    output["decision"] = "block"
    output["reason"] = render_selection(selection)
    output["suppressOriginalPrompt"] = True
    return output


def _selection_inactive(state: dict[str, Any]) -> bool:
    return (state.get("selection") or {}).get("status") in {
        "pending",
        "browser_pending",
        "disabled",
    }


def _selected_context(
    selection: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
    config: ClaudeCodeConfig | None = None,
) -> str:
    active_id = str((state or {}).get("taxonomy_id") or "").strip() or None
    store_dir = config.store_dir if config else Path()
    if config:
        manifest_id = ProgramWorkspace(config.trace_output).load().get("taxonomy_id")
        active_id = str(manifest_id or active_id or "").strip() or None
    return render_active_selection_context(
        selection,
        active_taxonomy_id=active_id,
        store_dir=store_dir,
    )


def _selection_accepted_context(
    selection: dict[str, Any],
    pending_task: str,
    *,
    state: dict[str, Any],
    config: ClaudeCodeConfig,
    original_prompt_continues: bool = False,
) -> str:
    context = _selected_context(selection, state=state, config=config)
    if original_prompt_continues:
        context += (
            " The submitted prompt is the held task. Continue it now without "
            "asking the user to repeat it."
        )
    elif pending_task:
        context += (
            " The user's held task follows. Continue it now without asking the "
            f"user to repeat it:\n\n{pending_task}"
        )
    else:
        context += " No task is held; acknowledge the choice and wait for a task."
    return context + "\n\n" + _standing_context(state, config)


def _standing_context(state: dict[str, Any], config: ClaudeCodeConfig) -> str:
    return STANDING_PROMPT + "\n\n" + checkpoint_transport_prompt(
        config.trace_output,
        str(state.get("session_id") or state.get("conversation_id") or ""),
        dashboard_url=state.get("dashboard_url"),
    )


def _refresh_conversation_metadata(
    state: dict[str, Any],
    event: dict[str, Any],
    config: ClaudeCodeConfig,
) -> bool:
    changed = False
    title = resolve_conversation_title(
        str(state.get("session_id") or event.get("session_id") or ""),
        event=event,
        existing=state.get("conversation_title"),
        prompt=state.get("episode_task"),
    )
    values = {
        "conversation_title": title,
        "conversation_host": "claude_code",
        "task_group": state.get("task_group") or config.task_group,
        "transcript_path": str(
            event.get("transcript_path") or state.get("transcript_path") or ""
        ),
    }
    for key, value in values.items():
        if value != state.get(key):
            state[key] = value
            changed = True
    if changed:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
    return changed


def _disabled_context(
    pending_task: str,
    *,
    original_prompt_continues: bool = False,
) -> str:
    context = (
        "AdaMAST is disabled for this conversation. Do not emit AdaMAST checkpoints, "
        "run AdaMAST gates, or record AdaMAST traces."
    )
    if original_prompt_continues:
        context += " Continue the submitted task now."
    elif pending_task:
        context += (
            " Continue the user's held task now without asking them to repeat it:\n\n"
            + pending_task
        )
    else:
        context += " Acknowledge the choice and wait for a task."
    return context


def _user_prompt(event: dict[str, Any]) -> str:
    for key in ("prompt", "user_prompt", "message", "text"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _browser_continuation_prompt(prompt: str) -> bool:
    return str(prompt or "").strip().casefold() in {
        "continue",
        "done",
        "selected",
        "i selected it",
        "selection complete",
    }


def _monitor_project_id(config: ClaudeCodeConfig) -> str | None:
    root = Path(config.routing_root or config.trace_output).expanduser().resolve()
    program = Path(config.trace_output).expanduser().resolve()
    try:
        parts = program.relative_to(root).parts
    except ValueError:
        return config.project_id
    if len(parts) >= 5 and parts[0] == "projects" and parts[2] == "groups":
        return parts[1]
    return config.project_id


def _required(event: dict[str, Any], name: str) -> str:
    value = str(event.get(name, "")).strip()
    if not value:
        raise ValueError(f"hook input is missing {name}")
    return value
