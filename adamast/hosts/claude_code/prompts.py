"""Standing and gate-specific prompts for Claude Code."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from string import Template
from typing import Any

from adamast.protocol.checkpoint_prompt import render_reflection_prompt


def _text_asset(name: str) -> str:
    return (
        resources.files("adamast.hosts.claude_code")
        .joinpath("assets").joinpath(name)
        .read_text(encoding="utf-8")
    )


STANDING_PROMPT = _text_asset("standing_prompt.md")


def checkpoint_transport_prompt(
    trace_output: Path | str,
    session_id: str,
    *,
    dashboard_url: str | None = None,
) -> str:
    """Render the Claude conversation's private checkpoint destination."""
    root = Path(trace_output).expanduser().resolve()
    lines = [
        "AdaMAST checkpoint recording is configured for this conversation.",
        (
            "At an existing AdaMAST gate, create the same four compact fields "
            "privately and record them with `adamast-claude-checkpoint`; do not "
            "print the checkpoint or the longer reflection in the conversation."
        ),
        f"Trace output: `{root}`",
        f"Conversation ID: `{session_id}`",
        "Recorder command prefix: `adamast-claude-checkpoint --trace-output "
        f'"{root}" --session-id "{session_id}"`',
        (
            "Use gate `stop` for the final gate, `task_completed` for a completed "
            "sub-task, `subagent_stop` for a subagent gate, and `tool_failure` "
            "after a failed tool. Supply `--checkpoint`, `--relevant-codes`, "
            "`--evidence`, and `--next-action` together. If recording fails, "
            "report the operational error instead of claiming capture."
        ),
    ]
    if dashboard_url:
        lines.append(f"Live AdaMAST monitor: {dashboard_url}")
    return "\n".join(lines)


def reflection_prompt(
    state: dict[str, Any],
    *,
    checkpoint_id: str,
    gate_label: str,
    recent_activity: str,
    full: bool,
    repair_attempts_used: int = 0,
) -> str:
    gate_tail = (
        Template(_text_asset("final_gate_tail.md")).substitute(
            repair_attempts_used=repair_attempts_used
        )
        if full else ""
    )
    return render_reflection_prompt(
        taxonomy_id=str(state["taxonomy_id"]),
        codes=state["taxonomy"]["codes"],
        checkpoint_id=checkpoint_id,
        gate_label=gate_label,
        recent_activity=recent_activity,
        full=full,
        final_instructions=gate_tail,
    )


def failure_nudge(
    state: dict[str, Any],
    *,
    checkpoint_id: str,
    failure_summary: str,
) -> str:
    taxonomy_context = reflection_prompt(
        state,
        checkpoint_id=checkpoint_id,
        gate_label="reactive failure nudge (advisory; non-blocking)",
        recent_activity=failure_summary,
        full=False,
    )
    return (
        taxonomy_context
        + "\n\nHandle this checkpoint privately. Record the four compact fields "
        "with the configured `adamast-claude-checkpoint` command using gate "
        "`tool_failure`. Do not print either checkpoint format in the "
        "conversation; continue the user's task after recording it."
    )
