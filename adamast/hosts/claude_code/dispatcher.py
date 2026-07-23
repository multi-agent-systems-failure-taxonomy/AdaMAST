"""Single command entry point registered for all Claude Code hooks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from adamast.hosts.claude_code.config import ClaudeCodeConfig
    from adamast.hosts.claude_code.learning_jobs import (
        claim_learning_job,
        drain_learning_notices,
        poll_learning_jobs,
        reconcile_learning_jobs,
    )
    from adamast.hosts.claude_code.custom import (
        custom_advisory,
        custom_blocking_checkpoint,
    )
    from adamast.hosts.claude_code.hooks import (
        post_tool_use,
        post_tool_use_failure,
        session_end,
        session_start,
        stop,
        subagent_stop,
        task_completed,
        user_prompt_submit,
    )
    from adamast.hosts.shared import force_utf8_stdio
else:
    from .config import ClaudeCodeConfig
    from .custom import custom_advisory, custom_blocking_checkpoint
    from .learning_jobs import (
        claim_learning_job,
        drain_learning_notices,
        poll_learning_jobs,
        reconcile_learning_jobs,
    )
    from .hooks import (
        post_tool_use,
        post_tool_use_failure,
        session_end,
        session_start,
        stop,
        subagent_stop,
        task_completed,
        user_prompt_submit,
    )
    from ..shared import force_utf8_stdio

HANDLERS = {
    "SessionStart": session_start.handle,
    "UserPromptSubmit": user_prompt_submit.handle,
    "SessionEnd": session_end.handle,
    "Stop": stop.handle,
    "TaskCompleted": task_completed.handle,
    "SubagentStop": subagent_stop.handle,
    "PostToolUse": post_tool_use.handle,
    "PostToolUseFailure": post_tool_use_failure.handle,
}


def main(argv=None) -> int:
    force_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--custom",
        default=None,
        help=(
            "name of a CustomHookSpec; when provided this dispatcher routes "
            "the incoming event through the user-declared hook instead of the "
            "built-in HANDLERS table"
        ),
    )
    args = parser.parse_args(argv)
    try:
        event = json.load(sys.stdin)
        base_config = ClaudeCodeConfig.load(args.config)
        config = base_config.for_event(event)
        if config.learning_backend == "provider" and config.openai_base_url:
            os.environ["OPENAI_BASE_URL"] = config.openai_base_url
        if config.learning_backend == "provider" and config.openai_api_key_env:
            value = os.environ.get(config.openai_api_key_env)
            if not value:
                raise RuntimeError(
                    f"credential environment variable "
                    f"{config.openai_api_key_env!r} is not set"
                )
            os.environ["OPENAI_API_KEY"] = value
        event_name = event.get("hook_event_name")
        if config.learning_backend == "claude_subagent":
            reconcile_learning_jobs(
                _workspace(config, event),
                store_dir=config.store_dir,
                trace_root=config.trace_root,
            )
        if args.custom:
            spec = config.find_custom_hook(args.custom)
            if spec is None:
                raise ValueError(
                    f"no CustomHookSpec named {args.custom!r} in "
                    f"{args.config}; rerun adamast-claude-add-hook or "
                    "adamast-claude-install to refresh"
                )
            if spec.mode == "blocking":
                code, output = custom_blocking_checkpoint(
                    event, config, spec=spec,
                )
            else:
                code, output = 0, custom_advisory(event, config, spec=spec)
        else:
            if event_name not in HANDLERS:
                raise ValueError(
                    f"unsupported Claude Code hook event {event_name!r}"
                )
            code, output = HANDLERS[event_name](event, config)
        if code == 0 and config.learning_backend == "claude_subagent":
            # A selector choice may have created a fresh per-conversation route.
            config = base_config.for_event(event)
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
            output = _merge_notices(
                output,
                event_name=str(event_name or "SessionStart"),
                notices=drain_learning_notices(
                    workspace,
                    _conversation_id(event),
                ),
            )
            if event_name in {"SessionStart", "UserPromptSubmit", "SubagentStop"}:
                dispatch = claim_learning_job(
                    workspace,
                    conversation_id=_conversation_id(event),
                    lease_seconds=config.worker_timeout_seconds,
                )
                if dispatch:
                    output = _merge_learning_context(
                        output,
                        event_name=str(event_name),
                        context=dispatch["directive"],
                    )
        if output:
            rendered = (
                json.dumps(output, ensure_ascii=False)
                if isinstance(output, dict)
                else str(output)
            )
            print(rendered, file=sys.stderr if code == 2 else sys.stdout)
        return code
    except Exception as exc:
        print(f"AdaMAST Claude Code hook error: {exc}", file=sys.stderr)
        return 1


def _workspace(config: ClaudeCodeConfig, event: dict):
    from adamast import ProgramWorkspace

    return ProgramWorkspace(config.trace_output, repo_path=event.get("cwd"))


def _conversation_id(event: dict) -> str:
    value = event.get("session_id") or event.get("conversation_id")
    if value:
        return str(value)
    return str(event.get("transcript_path") or "claude-session")


def _merge_notices(
    output: dict | str | None,
    *,
    event_name: str,
    notices: list[str],
) -> dict | str | None:
    if not notices:
        return output
    notice_text = "\n\n".join(item.strip() for item in notices if item.strip())
    if isinstance(output, dict):
        merged = dict(output)
    else:
        merged = {}
        if isinstance(output, str) and output.strip():
            merged["systemMessage"] = output.strip()
    messages = []
    current_message = merged.get("systemMessage")
    if isinstance(current_message, str) and current_message.strip():
        messages.append(current_message.strip())
    messages.append(notice_text)
    merged["systemMessage"] = "\n\n".join(messages)
    specific = dict(merged.get("hookSpecificOutput") or {})
    specific.setdefault("hookEventName", event_name)
    contexts = []
    current_context = specific.get("additionalContext")
    if isinstance(current_context, str) and current_context.strip():
        contexts.append(current_context.strip())
    contexts.append(notice_text)
    specific["additionalContext"] = "\n\n".join(contexts)
    merged["hookSpecificOutput"] = specific
    return merged


def _merge_learning_context(
    output: dict | str | None,
    *,
    event_name: str,
    context: str,
) -> dict:
    if isinstance(output, dict):
        merged = dict(output)
    else:
        merged = {}
        if isinstance(output, str) and output.strip():
            merged["systemMessage"] = output.strip()
    specific = dict(merged.get("hookSpecificOutput") or {})
    specific.setdefault("hookEventName", event_name)
    contexts = []
    existing = specific.get("additionalContext")
    if isinstance(existing, str) and existing.strip():
        contexts.append(existing.strip())
    contexts.append(context.strip())
    specific["additionalContext"] = "\n\n".join(contexts)
    merged["hookSpecificOutput"] = specific
    return merged


if __name__ == "__main__":
    raise SystemExit(main())
