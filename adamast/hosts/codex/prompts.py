"""Prompt assets for the Codex hook integration."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from string import Template
from typing import Any

from adamast.protocol.checkpoint_prompt import render_reflection_prompt


def _text_asset(name: str) -> str:
    return (
        resources.files("adamast.hosts.codex")
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
    """Render the conversation-specific direct checkpoint destination."""
    lines = [
        "AdaMAST checkpoint recording is configured for this conversation.",
        "At an existing AdaMAST gate, create the same four fields privately and ",
        "record them with `adamast-codex-checkpoint`; do not print the four-line ",
        "checkpoint in the user-facing conversation.",
        f"Trace output: `{Path(trace_output).expanduser().resolve()}`",
        f"Conversation ID: `{session_id}`",
        "Recorder command prefix: `adamast-codex-checkpoint --trace-output "
        f"\"{Path(trace_output).expanduser().resolve()}\" --session-id "
        f"\"{session_id}\"`",
        "Use gate `stop` for the final gate and `tool_failure` for the existing ",
        "failed-tool recovery gate. Supply `--checkpoint`, `--relevant-codes`, ",
        "`--evidence`, and `--next-action` together. If recording fails, report ",
        "that operational error instead of claiming the gate was captured.",
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
) -> str:
    gate_tail = (
        Template(_text_asset("final_gate_tail.md")).substitute(
            repair_attempts_used=0
        )
        if full
        else ""
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
