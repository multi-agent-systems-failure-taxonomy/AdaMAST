"""Codex hook runtime skin for AdaMAST."""

from __future__ import annotations

import hashlib
import json
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adamast.protocol.checkpoint import (
    checkpoint_line as _checkpoint_line,  # noqa: F401  (compat re-export)
    compact_checkpoint_fields as _compact_checkpoint_fields,  # noqa: F401
    compact_reflection,
    new_checkpoint_id,
    next_action_requires_repair as _next_action_requires_repair,  # noqa: F401
)
from adamast.hosts.codex.transcript import (
    external_workdirs,
    first_user_message,
    has_assistant_activity,
    latest_user_message,
    read_raw_transcript,
    resolve_conversation_title,
    trace_has_assistant_activity,
    transcript_size,
    user_messages,
)
from adamast import (
    GenerationTrace,
    ProgramWorkspace,
    Session,
    SessionDelivery,
    end_session,
    evaluate_pre_submission,
    redact_trace,
    start_session,
)
from adamast.core.evidence import annotate_checkpoints
from adamast.core.reflection import (
    ReflectionResult,
    parse_reflection,
)
from adamast.core import mast, resolver

from adamast.hosts.shared import build_session_state
from adamast.hosts.codex.learning_jobs import (
    LearningJobError,
    capture_learning_receipt,
    enqueue_learning_job,
)
from adamast.hosts.codex.browser_picker import (
    allowed_option,
    apply_browser_choice,
    open_browser_picker,
    picker_alive,
    read_browser_choice,
    start_browser_picker,
    wait_for_browser_choice,
)
from adamast.hosts.interactive.selector import (
    SELECTOR_VERSION,
    build_selection,
    parse_selection_choice,
    render_active_selection_context,
    selection_interstitial,
)
from adamast.hosts.interactive.source import (
    conversation_source,
    require_compatible_taxonomy,
    stamp_program_source,
)

from .config import CodexConfig
from .prompts import STANDING_PROMPT, checkpoint_transport_prompt
from .state import load_state, save_state


def session_start(event: dict[str, Any], config: CodexConfig) -> dict | None:
    session_id = _session_id(event)
    existing = load_state(config.trace_output, session_id)
    if existing:
        title = resolve_conversation_title(
            session_id,
            event=event,
            existing=existing.get("conversation_title"),
        )
        if title != existing.get("conversation_title"):
            existing["conversation_title"] = title
            save_state(config.trace_output, session_id, existing)
    recovered = False
    if existing and not existing.get("finished") and existing.get("lifecycle"):
        _finish_runtime_session(
            existing,
            config,
            transcript_path=event.get("transcript_path"),
            reason="session_resume_recovery",
        )
        existing["pending"] = {}
        save_state(config.trace_output, session_id, existing)
        recovered = True
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
                    return _add_context(
                        _selected_context(
                            selection,
                            state=existing,
                            config=config,
                        )
                        + "\n\n"
                        + _standing_context(existing, config),
                        event_name="SessionStart",
                        system_message=(
                            "AdaMAST recovered the conversation's previous "
                            "taxonomy choice."
                        ),
                    )
                selection = _refresh_pending_selection(
                    existing,
                    event,
                    config,
                )
                save_state(config.trace_output, session_id, existing)
                if config.selector_surface == "inline":
                    return _selection_output("SessionStart", selection)
                return None
            if status == "browser_pending":
                return _browser_waiting_output(selection, "SessionStart")
            if status == "disabled":
                return {
                    "systemMessage": "AdaMAST is disabled for this conversation."
                }
            return _add_context(
                _selected_context(selection, state=existing, config=config)
                + "\n\n"
                + _standing_context(existing, config),
                event_name="SessionStart",
                system_message=(
                    "AdaMAST recovered and closed the previous unfinished episode."
                    if recovered
                    else None
                ),
            )
        if not existing:
            # Codex also emits SessionStart for short-lived internal agent
            # sessions. Opening the browser here makes those invisible host
            # tasks look like new user conversations. Browser selection starts
            # on the first real UserPromptSubmit instead.
            if config.selector_surface == "browser":
                return None
            state = _new_selector_state(event, config)
            selection = state["selection"]
            save_state(config.trace_output, session_id, state)
            if config.selector_surface == "inline":
                return _selection_output("SessionStart", selection)
            return _launch_selection_browser(
                state,
                event,
                config,
                event_name="SessionStart",
            )

    if recovered:
        return _add_context(
            _standing_context(existing, config),
            event_name="SessionStart",
            system_message=(
                "AdaMAST recovered and closed the previous unfinished episode."
            ),
        )

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
    return _add_context(context)


def _new_selector_state(
    event: dict[str, Any],
    config: CodexConfig,
) -> dict[str, Any]:
    session_id = _session_id(event)
    cursor = transcript_size(event.get("transcript_path"))
    title = resolve_conversation_title(session_id, event=event)
    source = conversation_source("codex", event, title=title)
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
        "conversation_host": "codex",
        "task_group": config.task_group,
        "conversation_title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "episode_sequence": 0,
        "main_cursor": cursor,
        "episode_cursor": cursor,
        "selection": build_selection(
            trace_output=config.trace_output,
            store_dir=config.store_dir,
            cwd=event.get("cwd"),
            catalog_mode=config.selector_surface,
            host="codex",
            source=source,
        ),
        "finished": True,
        "trace_captured": False,
    }


def _start_episode(
    event: dict[str, Any],
    config: CodexConfig,
    *,
    sequence: int,
    cursor: int,
    previous: dict[str, Any] | None = None,
    taxonomy_id: str | None = None,
    episode_task: str | None = None,
) -> tuple[dict[str, Any], Session]:
    """Start one runtime task inside a longer Codex conversation."""
    session_id = _session_id(event)
    title = resolve_conversation_title(
        session_id,
        event=event,
        existing=(previous or {}).get("conversation_title"),
        prompt=episode_task,
    )
    source = conversation_source(
        "codex",
        event,
        title=title,
        prompt=episode_task,
    )
    stamp_program_source(
        ProgramWorkspace(config.trace_output, repo_path=event.get("cwd")),
        source,
        store_dir=config.store_dir,
    )

    # A selector choice establishes the project/group root. Once generation or
    # refinement activates a successor, later episodes follow that shared
    # binding instead of trying to pin the conversation to its original id.
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
        host="codex",
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
        session_id=f"codex:{session_id}:episode:{sequence}",
        adamast_model=config.adamast_model,
        repo_path=event.get("cwd") or Path.cwd(),
        max_retries=config.max_retries,
        dashboard=False,
        generation_threshold=config.generation_threshold,
        # Hook processes are killed at the harness's hook timeout, so
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
        max_retries=config.max_retries,
        main_cursor=cursor,
        episode_sequence=sequence,
        episode_cursor=cursor,
        failure={
            "call_index": 0,
        },
    )
    state["conversation_id"] = session_id
    state["conversation_host"] = "codex"
    state["task_group"] = config.task_group
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


def user_prompt_submit(event: dict[str, Any], config: CodexConfig) -> dict | None:
    """Resolve the Codex selector and start the chosen episode."""
    session_id = _session_id(event)
    state = load_state(config.trace_output, session_id)
    if not state:
        session_start({**event, "hook_event_name": "SessionStart"}, config)
        state = load_state(config.trace_output, session_id)
    if (
        not state
        and config.session_selector == "prompt"
        and config.inherit is None
        and config.selector_surface == "browser"
    ):
        state = _new_selector_state(event, config)
        save_state(config.trace_output, session_id, state)
    prompt = _user_prompt(event)
    if state:
        title = resolve_conversation_title(
            session_id,
            event=event,
            existing=state.get("conversation_title"),
            prompt=prompt,
        )
        if title != state.get("conversation_title"):
            state["conversation_title"] = title
            save_state(config.trace_output, session_id, state)

    recovered = False
    if (
        prompt
        and state.get("lifecycle")
        and not state.get("finished")
        and has_assistant_activity(
            event.get("transcript_path"),
            after=int(state.get("episode_cursor", 0)),
        )
    ):
        _finish_runtime_session(
            state,
            config,
            transcript_path=event.get("transcript_path"),
            reason="next_user_prompt_recovery",
            exclude_trailing_user=prompt,
        )
        state["pending"] = {}
        save_state(config.trace_output, session_id, state)
        recovered = True

    if config.session_selector != "prompt" or config.inherit is not None:
        if not prompt:
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
        if recovered:
            return _add_context(
                _standing_context(state, config),
                event_name="UserPromptSubmit",
                system_message=(
                    "AdaMAST recovered the previous episode and started a new one."
                ),
            )
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
                choice = allowed_option(selection, taxonomy_id, config.store_dir)
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
            return _selection_output("UserPromptSubmit", selection)

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
        return _add_context(
            _selected_context(selection, state=fresh, config=config)
            + "\n\n"
            + _standing_context(fresh, config),
            event_name="UserPromptSubmit",
        )
    return None


def _accept_selection_choice(
    state: dict[str, Any],
    event: dict[str, Any],
    config: CodexConfig,
    choice: dict[str, Any],
    *,
    original_prompt_continues: bool = False,
) -> dict:
    """Activate one selector choice while preserving the held first prompt."""
    session_id = _session_id(event)
    selection = state.get("selection") or {}
    selection["selected_kind"] = choice["kind"]
    selection["selected_taxonomy_id"] = choice.get("taxonomy_id")
    selection["selected_label"] = choice["label"]
    pending_task = str(selection.get("pending_task") or "").strip()
    if choice["kind"] == "disabled":
        selection["status"] = "disabled"
        state["finished"] = True
        save_state(config.trace_output, session_id, state)
        return _add_context(
            _disabled_context(
                pending_task,
                original_prompt_continues=original_prompt_continues,
            ),
            event_name="UserPromptSubmit",
            system_message="AdaMAST disabled for this conversation.",
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
            source_state["selection"] = {
                **selection,
                "status": "routed",
            }
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
    return _add_context(
        _selection_accepted_context(
            selection,
            pending_task,
            state=(fresh if pending_task else state),
            config=target_config,
            original_prompt_continues=original_prompt_continues,
        ),
        event_name="UserPromptSubmit",
        system_message=f"AdaMAST selected {choice['label']}.",
    )


def _ensure_active_episode(
    event: dict[str, Any],
    config: CodexConfig,
    state: dict[str, Any],
) -> dict[str, Any]:
    selection = state.get("selection") or {}
    if selection.get("status") in {"pending", "browser_pending", "disabled"}:
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


def stop(event: dict[str, Any], config: CodexConfig) -> dict | None:
    """Commit a Codex episode from a privately recorded gate in one callback.

    Codex documents Stop continuation, but some desktop builds complete the
    task after rendering the continuation response without invoking Stop a
    second time. Missing private gates are recorded as monitor warnings rather
    than harvested from user-visible assistant text.
    """
    state = _state(event, config)
    selection = state.get("selection") or {}
    if selection.get("status") in {"pending", "browser_pending"}:
        return {
            "continue": True,
            "systemMessage": "AdaMAST is waiting for taxonomy selection.",
        }
    if selection.get("status") == "disabled":
        return None
    state = _ensure_active_episode(event, config, state)
    if state.get("finished"):
        return {
            "continue": True,
            "systemMessage": "AdaMAST episode already committed.",
        }

    episode_sequence = int(state.get("episode_sequence", 1))
    recorded = [
        item
        for item in state.get("recorded_checkpoints", [])
        if isinstance(item, dict)
        and int(item.get("episode_sequence", -1)) == episode_sequence
    ]
    staged_final = next(
        (item for item in reversed(recorded) if item.get("gate") == "stop"),
        None,
    )
    if staged_final:
        gate_status = str(staged_final.get("status") or "READY_TO_SUBMIT")
        gate_error = staged_final.get("error")
    else:
        gate_status = "MISSING_PRIVATE_CHECKPOINT"
        gate_error = "private recorder was not called before Stop"

    checkpoint_ids = [
        str(item.get("checkpoint_id"))
        for item in recorded
        if item.get("checkpoint_id")
    ]
    annotate_checkpoints(
        config.trace_output,
        checkpoint_ids,
        turn_id=(str(event["turn_id"]) if event.get("turn_id") else None),
        gate_status=gate_status,
    )

    state["gate_result"] = {
        "status": gate_status,
        "error": gate_error,
        "stop_hook_active": bool(event.get("stop_hook_active")),
        "turn_id": event.get("turn_id"),
        "checkpoint_id": staged_final.get("checkpoint_id") if staged_final else None,
    }
    state.setdefault("pending", {}).pop("stop:main", None)
    _finish_runtime_session(
        state,
        config,
        transcript_path=event.get("transcript_path"),
        reason=(
            "stop_gate"
            if gate_status == "READY_TO_SUBMIT"
            else "stop_gate_unresolved"
            if gate_status == "REPAIR_REQUIRED"
            else "stop_gate_missing_checkpoint"
        ),
    )
    state["main_cursor"] = transcript_size(event.get("transcript_path"))
    save_state(config.trace_output, state["session_id"], state)

    if gate_status == "READY_TO_SUBMIT":
        message = None
    elif gate_status == "REPAIR_REQUIRED":
        message = (
            "AdaMAST episode trace committed with an unresolved repair checkpoint."
        )
    else:
        message = (
            "AdaMAST episode trace committed, but its private final checkpoint "
            "was not recorded. Details are available in the monitor."
        )
    warning = state.get("project_scope_warning")
    if warning:
        message = ((message + "\n\n") if message else "") + str(warning)
    return {
        "continue": True,
        **({"systemMessage": message} if message else {}),
    }


def subagent_stop(event: dict[str, Any], config: CodexConfig) -> dict | None:
    workspace = ProgramWorkspace(config.trace_output, repo_path=event.get("cwd"))
    try:
        learning_job_id = capture_learning_receipt(workspace, event)
    except (LearningJobError, OSError, ValueError) as exc:
        return {
            "continue": True,
            "systemMessage": f"AdaMAST ignored an invalid taxonomy receipt: {exc}",
        }
    if learning_job_id:
        return {
            "continue": True,
            "systemMessage": (
                f"AdaMAST taxonomy proposal received for {learning_job_id}; "
                "validation is pending."
            ),
        }
    state = _state(event, config)
    if _selection_inactive(state) or state.get("finished"):
        return None
    # Ordinary subagent checkpoints also use the private recorder. Never parse
    # a subagent's visible response as AdaMAST evidence.
    return None


def post_tool_use(event: dict[str, Any], config: CodexConfig) -> dict | None:
    """Record a successful-tool poll without claiming model-context delivery.

    Codex documents PostToolUse as a turn-scoped hook with visible hook status,
    but not as an additionalContext delivery point. Failure reflection lives in
    the always-loaded AdaMAST skill, where the agent can react to its tool result.
    """
    state = _state(event, config)
    if _selection_inactive(state):
        return None
    state = _ensure_active_episode(event, config, state)
    if state.get("finished"):
        return None
    failure = state.setdefault("failure", {})
    failure["call_index"] = int(failure.get("call_index", 0)) + 1
    save_state(config.trace_output, state["session_id"], state)
    return None


def _harvest_codex_checkpoint(
    text: str,
    state: dict[str, Any],
    *,
    gate: str = "stop",
) -> tuple[ReflectionResult | None, str, str | None]:
    """Validate private recorder input, including legacy full reflections."""
    pending = (state.get("pending") or {}).get("stop:main")
    if pending:
        try:
            reflection = parse_reflection(
                text,
                checkpoint_id=str(pending["checkpoint_id"]),
                known_code_ids=_code_ids(state),
            )
        except ValueError:
            pass
        else:
            decision = evaluate_pre_submission(
                text,
                max_retries=int(state["max_retries"]),
                repair_attempts_used=int(pending.get("repairs_completed", 0)),
            )
            status = (
                decision.status
                if decision.status in {"READY_TO_SUBMIT", "REPAIR_REQUIRED"}
                else "READY_TO_SUBMIT"
            )
            return reflection, status, None

    # Compact-transport validation, citation matching, and the repair
    # heuristic are the one shared implementation in adamast.protocol.
    return compact_reflection(text, state, gate=gate)


def _without_trailing_user(raw_trajectory: str, prompt: str) -> str:
    lines = str(raw_trajectory or "").splitlines()
    for index in range(len(lines) - 1, -1, -1):
        try:
            item = json.loads(lines[index])
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") == "user" and str(item.get("text") or "").strip() == prompt.strip():
            del lines[index]
        break
    return "\n".join(lines).strip()


def _finish_runtime_session(
    state: dict[str, Any],
    config: CodexConfig,
    *,
    transcript_path: str | None,
    reason: str,
    exclude_trailing_user: str | None = None,
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
        # Hook processes are killed at the harness's hook timeout: learning
        # must never run inline here. Background workers only, regardless of
        # the configured *_stops flags.
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
    if not persisted_trace_names:
        raw_trajectory = read_raw_transcript(
            transcript_path,
            after=cursor,
        ).strip()
        if exclude_trailing_user:
            raw_trajectory = _without_trailing_user(
                raw_trajectory,
                exclude_trailing_user,
            )
        capture_trajectory = (
            raw_trajectory
            if trace_has_assistant_activity(raw_trajectory)
            else ""
        )
        external = external_workdirs(
            capture_trajectory,
            bound_root=state.get("cwd"),
        )
        if external:
            state["project_scope_warning"] = (
                "AdaMAST project scope mismatch: this conversation is bound to "
                f"{state.get('cwd') or 'an unknown root'}, but tool work explicitly "
                f"ran in {', '.join(external)}. Start the next task from the actual "
                "repository or configure a stable codex.project_id before collecting "
                "more shared taxonomy traces."
            )
        else:
            state.pop("project_scope_warning", None)

    if not persisted_trace_names and capture_trajectory:
        task = str(state.get("episode_task") or "").strip() or first_user_message(
            transcript_path, after=cursor
        ).strip() or (
            f"Codex episode {sequence} in "
            f"{state.get('cwd') or 'unknown working directory'}"
        )
        trace = GenerationTrace(
            problem_id=f"codex:{state['session_id']}:episode:{sequence}",
            task=task,
            raw_trajectory=capture_trajectory,
            metadata={
                "harness": "codex",
                "codex_session_id": state["session_id"],
                "conversation_id": state["session_id"],
                "episode_sequence": sequence,
                "trace_granularity": "episode",
                "runtime_session_id": state["runtime_session_id"],
                "taxonomy_id": state["taxonomy_id"],
                "end_reason": reason,
                "transcript_format": "codex_normalized_jsonl_v1",
                "external_workdirs": external,
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
    if config.learning_backend == "codex_subagent":
        common = {
            "store_dir": config.store_dir,
            "trace_root": config.trace_root,
            "task_group": config.task_group,
            "conversation_id": str(state["session_id"]),
            "worker_model": config.worker_model,
            "worker_timeout_seconds": config.worker_timeout_seconds,
        }
        def generation_launcher():
            return enqueue_learning_job(
                workspace,
                kind="generation",
                **common,
            )

        def refinement_launcher():
            return enqueue_learning_job(
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


def _state(event: dict[str, Any], config: CodexConfig) -> dict[str, Any]:
    session_id = _session_id(event)
    state = load_state(config.trace_output, session_id)
    if not state:
        session_start(event, config)
        state = load_state(config.trace_output, session_id)
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
    config: CodexConfig,
) -> dict[str, Any] | None:
    """Migrate an exact inline choice missed by Codex Desktop hooks."""
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
            "session_id": _session_id(event),
            "trace_output": str(config.trace_output),
            "store_dir": str(config.store_dir),
            "selection": selection,
            "event": {
                "cwd": event.get("cwd"),
                "session_id": _session_id(event),
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
    recovered = load_state(Path(receipt["trace_output"]), _session_id(event))
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
    save_state(Path(receipt["trace_output"]), _session_id(event), recovered)
    return recovered


def _session_id(event: dict[str, Any]) -> str:
    for key in ("session_id", "thread_id", "conversation_id"):
        value = event.get(key)
        if value:
            return str(value)
    cwd = str(event.get("cwd") or Path.cwd())
    return hashlib.sha256(cwd.encode("utf-8", "replace")).hexdigest()[:16]


def _monitor_project_id(config: CodexConfig) -> str | None:
    root = Path(config.routing_root or config.trace_output).expanduser().resolve()
    program = Path(config.trace_output).expanduser().resolve()
    try:
        parts = program.relative_to(root).parts
    except ValueError:
        return config.project_id
    if len(parts) >= 5 and parts[0] == "projects" and parts[2] == "groups":
        return parts[1]
    return config.project_id


def _checkpoint_id(gate: str) -> str:
    return new_checkpoint_id(gate)


def _code_ids(state: dict[str, Any]) -> set[str]:
    return {
        str(code.get("id"))
        for code in state.get("taxonomy", {}).get("codes", [])
        if isinstance(code, dict) and code.get("id")
    }


def _add_context(
    context: str,
    *,
    event_name: str | None = None,
    system_message: str | None = None,
) -> dict:
    specific = {"additionalContext": context}
    if event_name:
        specific["hookEventName"] = event_name
    output = {"hookSpecificOutput": specific}
    if system_message:
        output["systemMessage"] = system_message
    return output


def _selection_output(event_name: str, selection: dict[str, Any]) -> dict:
    return _add_context(
        selection_interstitial(selection),
        event_name=event_name,
        system_message="AdaMAST taxonomy selection required.",
    )


def _refresh_pending_selection(
    state: dict[str, Any],
    event: dict[str, Any],
    config: CodexConfig,
) -> dict[str, Any]:
    current = state.get("selection") or {}
    if int(current.get("version") or 0) >= SELECTOR_VERSION:
        return current
    refreshed = build_selection(
        trace_output=config.trace_output,
        store_dir=config.store_dir,
        cwd=event.get("cwd"),
        catalog_mode=config.selector_surface,
        host="codex",
        source=conversation_source(
            "codex",
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
    config: CodexConfig,
    *,
    event_name: str,
) -> dict:
    selection = state.get("selection") or {}
    try:
        picker = start_browser_picker(
            config.trace_output,
            _session_id(event),
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
        return _add_context(
            "AdaMAST could not open its local taxonomy library. Do not perform "
            "the held task yet; report the selector error to the user.",
            event_name=event_name,
            system_message=f"AdaMAST could not open the taxonomy library: {exc}",
        )
    selection["status"] = "browser_pending"
    selection["browser_picker"] = picker
    state["selection"] = selection
    save_state(config.trace_output, _session_id(event), state)
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


def _browser_opened_output(selection: dict[str, Any], event_name: str) -> dict:
    picker = selection.get("browser_picker") or {}
    url = str(picker.get("url") or "the local AdaMAST catalog")
    return _add_context(
        "AdaMAST taxonomy selection is waiting in the local browser. The catalog "
        f"opened at {url}. Do not perform the held task yet. Ask the user to "
        "choose a taxonomy there. The browser applies it directly to this "
        "conversation; return to Codex when the page confirms activation.",
        event_name=event_name,
        system_message="AdaMAST taxonomy library opened in the browser.",
    )


def _browser_waiting_output(
    selection: dict[str, Any],
    event_name: str,
) -> dict:
    picker = selection.get("browser_picker") or {}
    url = str(picker.get("url") or "the local AdaMAST catalog")
    return _add_context(
        "AdaMAST is still waiting for a taxonomy choice in the local browser at "
        f"{url}. Do not perform the held task yet. Ask the user to finish the "
        "browser selection and send another message.",
        event_name=event_name,
        system_message="AdaMAST is waiting for the browser taxonomy selection.",
    )


def _browser_timeout_output(
    selection: dict[str, Any],
    event_name: str,
) -> dict:
    picker = selection.get("browser_picker") or {}
    url = str(picker.get("url") or "the local AdaMAST catalog")
    reason = (
        "AdaMAST taxonomy selection timed out before the local browser at "
        f"{url} returned a choice. Submit the task again to reopen "
        "the taxonomy library."
    )
    return {
        "continue": False,
        "stopReason": reason,
        "systemMessage": "AdaMAST taxonomy selection timed out; submit the task again.",
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": reason,
        },
    }


def _selection_inactive(state: dict[str, Any]) -> bool:
    return (state.get("selection") or {}).get("status") in {
        "pending",
        "browser_pending",
        "disabled",
    }


def _browser_continuation_prompt(prompt: str) -> bool:
    return str(prompt or "").strip().casefold() in {
        "continue",
        "done",
        "selected",
        "i selected it",
        "selection complete",
    }


def _selected_context(
    selection: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
    config: CodexConfig | None = None,
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
    config: CodexConfig,
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


def _standing_context(state: dict[str, Any], config: CodexConfig) -> str:
    return STANDING_PROMPT + "\n\n" + checkpoint_transport_prompt(
        config.trace_output,
        str(state.get("session_id") or state.get("conversation_id") or ""),
        dashboard_url=state.get("dashboard_url"),
    )


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
    transcript_prompt = latest_user_message(event.get("transcript_path"))
    if transcript_prompt:
        return transcript_prompt
    for key in ("prompt", "user_prompt", "message", "text"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            prompt = value.strip()
            if not _internal_prompt(prompt):
                return prompt
    return ""


def _internal_prompt(prompt: str) -> bool:
    lowered = str(prompt or "").lstrip().lower()
    return lowered.startswith(
        (
            "<hook_prompt",
            "<environment_context",
            "<skills_instructions",
            "<permissions instructions",
            "<app-context",
        )
    )


def decisions_log(config: CodexConfig, event: dict[str, Any], output: dict | None) -> None:
    path = Path(config.trace_output) / "codex-decisions.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8") if not path.exists() else None
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": event.get("hook_event_name"),
                    "session_id": _session_id(event),
                    "output": output,
                },
                ensure_ascii=False,
                default=str,
            )
            + "\n"
        )
