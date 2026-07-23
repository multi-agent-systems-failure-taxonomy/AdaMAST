"""Single command entry point registered for Codex lifecycle hooks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:  # pragma: no cover - script execution fallback
    from adamast.hosts.codex.config import CodexConfig
    from adamast.hosts.codex.learning_jobs import (
        claim_learning_job,
        drain_learning_notices,
        poll_learning_jobs,
        reconcile_learning_jobs,
    )
    from adamast.hosts.codex.runtime import (
        decisions_log,
        post_tool_use,
        session_start,
        stop,
        subagent_stop,
        user_prompt_submit,
    )
    from adamast.hosts.codex.state import load_state
    from adamast.hosts.shared import force_utf8_stdio
except ModuleNotFoundError:  # pragma: no cover
    from .config import CodexConfig
    from .learning_jobs import (
        claim_learning_job,
        drain_learning_notices,
        poll_learning_jobs,
        reconcile_learning_jobs,
    )
    from .runtime import (
        decisions_log,
        post_tool_use,
        session_start,
        stop,
        subagent_stop,
        user_prompt_submit,
    )
    from .state import load_state
    from ..shared import force_utf8_stdio

HANDLERS = {
    "SessionStart": session_start,
    "UserPromptSubmit": user_prompt_submit,
    "Stop": stop,
    "SubagentStop": subagent_stop,
    "PostToolUse": post_tool_use,
}

AGENT_VISIBLE_EVENTS = {"SessionStart", "UserPromptSubmit"}


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description="AdaMAST Codex hook dispatcher.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--event")
    args = parser.parse_args(argv)
    event: dict = {}
    config: CodexConfig | None = None
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if _is_internal_codex_event(event):
            return 0
        base_config = CodexConfig.load(args.config)
        config = base_config.for_event(event)
        if config.learning_backend == "provider" and config.openai_base_url:
            os.environ["OPENAI_BASE_URL"] = config.openai_base_url
        if config.learning_backend == "provider" and config.openai_api_key_env:
            value = os.environ.get(config.openai_api_key_env)
            if not value:
                raise RuntimeError(
                    f"configured openai_api_key_env "
                    f"{config.openai_api_key_env!r} is not set"
                )
            os.environ["OPENAI_API_KEY"] = value
        event_name = args.event or event.get("hook_event_name")
        if event_name not in HANDLERS:
            raise RuntimeError(f"unsupported Codex hook event: {event_name!r}")
        if config.learning_backend == "codex_subagent":
            reconcile_learning_jobs(
                _workspace(config, event),
                store_dir=config.store_dir,
                trace_root=config.trace_root,
            )
        output = HANDLERS[event_name](event, config)
        # Selection may have created a fresh per-conversation route.
        config = base_config.for_event(event)
        if (
            config.learning_backend == "codex_subagent"
            and _conversation_learning_active(config, event)
        ):
            workspace = _workspace(config, event)
            poll_learning_jobs(
                workspace,
                store_dir=config.store_dir,
                trace_root=config.trace_root,
                task_group=config.task_group,
                conversation_id=_conversation_id(event),
                generation_threshold=config.generation_threshold,
                k_init=config.k_init,
                k=config.k,
                freeze=config.freeze,
                worker_model=config.worker_model,
                worker_timeout_seconds=config.worker_timeout_seconds,
            )
            reconcile_learning_jobs(
                workspace,
                store_dir=config.store_dir,
                trace_root=config.trace_root,
            )
            if event_name in AGENT_VISIBLE_EVENTS:
                output = _merge_notices(
                    output,
                    drain_learning_notices(workspace, _conversation_id(event)),
                )
            if event_name in {"SessionStart", "UserPromptSubmit"}:
                dispatch = claim_learning_job(
                    workspace,
                    conversation_id=_conversation_id(event),
                    lease_seconds=config.worker_timeout_seconds,
                )
                if dispatch:
                    output = _merge_learning_context(output, dispatch["directive"])
        decisions_log(config, event, output)
        if output:
            print(json.dumps(output, ensure_ascii=False))
        return 0
    except Exception as exc:
        if config is not None:
            try:
                decisions_log(
                    config,
                    event,
                    {
                        "hookError": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            except Exception:
                pass
        print(f"AdaMAST Codex hook failed: {exc}", file=sys.stderr)
        return 1


def _is_internal_codex_event(event: dict) -> bool:
    """Exclude host-maintenance tasks that are not user conversations."""
    cwd = str(event.get("cwd") or "").strip()
    if not cwd:
        return False
    codex_home = Path(
        os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    ).expanduser()
    try:
        relative = Path(cwd).expanduser().resolve().relative_to(
            codex_home.resolve()
        )
    except (OSError, ValueError):
        return False
    return bool(relative.parts and relative.parts[0].casefold() == "memories")


def _workspace(config: CodexConfig, event: dict):
    from adamast import ProgramWorkspace

    return ProgramWorkspace(config.trace_output, repo_path=event.get("cwd"))


def _conversation_id(event: dict) -> str:
    for key in ("session_id", "thread_id", "conversation_id"):
        value = event.get(key)
        if value:
            return str(value)
    transcript = event.get("transcript_path")
    if transcript:
        return str(transcript)
    return "codex-session"


def _conversation_learning_active(config: CodexConfig, event: dict) -> bool:
    """Keep learning work attached to selected user conversations only."""
    if config.session_selector != "prompt" or config.inherit is not None:
        return True
    state = load_state(config.trace_output, _conversation_id(event))
    selection = state.get("selection") or {}
    return bool(
        selection.get("status") == "selected" and selection.get("selected_taxonomy_id")
    )


def _merge_notices(output: dict | None, notices: list[str]) -> dict | None:
    notices = [notice.strip() for notice in notices if notice.strip()]
    if not notices:
        return output
    merged = dict(output or {})
    merged.setdefault("continue", True)
    messages = []
    existing = merged.get("systemMessage")
    if isinstance(existing, str) and existing.strip():
        messages.append(existing.strip())
    messages.extend(notices)
    merged["systemMessage"] = "\n\n".join(messages)
    specific = dict(merged.get("hookSpecificOutput") or {})
    contexts = []
    existing_context = specific.get("additionalContext")
    if isinstance(existing_context, str) and existing_context.strip():
        contexts.append(existing_context.strip())
    notice_text = "\n\n".join(notices)
    contexts.append(
        "AdaMAST learning lifecycle update. Before the next tool call, show the "
        "user this state change once in a concise progress update. Do not "
        "repeat it on later turns:\n\n" + notice_text
    )
    specific["additionalContext"] = "\n\n".join(contexts)
    merged["hookSpecificOutput"] = specific
    return merged


def _merge_learning_context(output: dict | None, context: str) -> dict:
    merged = dict(output or {})
    specific = dict(merged.get("hookSpecificOutput") or {})
    existing = specific.get("additionalContext")
    contexts = []
    if isinstance(existing, str) and existing.strip():
        contexts.append(existing.strip())
    contexts.append(context.strip())
    specific["additionalContext"] = "\n\n".join(contexts)
    merged["hookSpecificOutput"] = specific
    merged.setdefault("continue", True)
    return merged


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
