"""Shared AdaMAST checkpoint-reflection prompt body."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import resources
from string import Template
from typing import Any


def _text_asset(name: str) -> str:
    return (
        resources.files("adamast.protocol")
        .joinpath("assets").joinpath(name)
        .read_text(encoding="utf-8")
    )


def render_reflection_prompt(
    *,
    taxonomy_id: str,
    codes: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    checkpoint_id: str,
    gate_label: str,
    recent_activity: str,
    full: bool,
    final_instructions: str = "",
) -> str:
    """Render the agent-agnostic checkpoint reflection prompt."""
    code_list = "\n".join(
        f"- {code['id']} — {code['name']}: {code['description']}"
        for code in codes
    )
    scope = "the full task trajectory" if full else (
        "only the recent activity since the previous AdaMAST checkpoint"
    )
    return Template(_text_asset("checkpoint_reflection.md")).substitute(
        gate_label=gate_label,
        checkpoint_id=checkpoint_id,
        taxonomy_id=taxonomy_id,
        code_list=code_list,
        scope=scope,
        recent_activity=(
            recent_activity[-12000:]
            or "(no transcript text was available; use the activity in context)"
        ),
        final_instructions=final_instructions,
    )


def render_format_repair(
    *,
    checkpoint_id: str,
    issues: Sequence[str],
    full: bool,
) -> str:
    """Render the targeted re-prompt for a reflection that failed only on form.

    Recovering from a form failure must never re-sample content: the prompt
    names only the missing/invalid elements and instructs the agent to keep
    its previous codes and verdict.
    """
    issue_lines = "\n".join(f"- {issue}" for issue in issues) or (
        "- the reflection block could not be validated"
    )
    return Template(_text_asset("format_repair.md")).substitute(
        checkpoint_id=checkpoint_id,
        issues=issue_lines,
        keep_status=(
            ", the Decide outcome, and the `Final AdaMAST status`"
            if full
            else " and the Decide outcome"
        ),
        gate_reminder=(
            "\n\nAfter the block, re-emit the final gate fields "
            "(`Final AdaMAST status:`, `Codes checked:`, `Evidence:`, "
            "`Repair attempts used:`, `Final decision:`) with the same "
            "status as before."
            if full
            else ""
        ),
    )
