"""Prompt asset loader/renderers for the AdaMAST Reflection Judge."""

from __future__ import annotations

import json
from importlib import resources
from string import Template
from typing import Any, Mapping

JUDGE_PROMPT_VERSION = "v1"


def _asset(name: str) -> str:
    return (
        resources.files("adamast.judges.reflection_judge")
        .joinpath("assets").joinpath(name)
        .read_text(encoding="utf-8")
    )


ANALYSIS_SYSTEM = _asset("analysis_system.md")
MAPPING_SYSTEM = _asset("mapping_system.md")
SINGLE_CALL_SYSTEM = _asset("single_call_system.md")


def _enum(name: str, values) -> str:
    return f"{name} — one of: {', '.join(sorted(values))}"


def analysis_user_prompt(judge_input: Mapping[str, Any]) -> str:
    """Render the analysis user prompt for a single trace."""
    ji = judge_input
    return Template(_asset("analysis_user.md")).substitute(
        task_id=repr(ji.get("task_id")),
        candidate_id=repr(ji.get("candidate_id")),
        run_id=repr(ji.get("run_id")),
        task_prompt=ji.get("task_prompt") or "(not provided)",
        expected_output=ji.get("expected_output") or "(not provided)",
        candidate_output=ji.get("candidate_output") or "(not provided)",
        score=repr(ji.get("score")),
        trace=ji.get("trace"),
        reason_observed_or_inferred=_enum("", ["observed", "inferred", "mixed", "unclear"]),
        evidence_strength=_enum("", ["low", "medium", "high", "direct"]),
        relation_type=_enum("relation_type", ["caused", "contributed_to", "enabled", "amplified", "masked", "recovered", "partially_recovered", "made_irrelevant"]),
        recovery_status=_enum("", ["unrecovered", "partially_recovered", "fully_recovered", "made_irrelevant", "unclear", "not_applicable"]),
        recovery_source=_enum("", ["same_agent", "checker", "refiner", "arbiter", "tool_result", "external_feedback", "later_strategy_change", "none", "unclear", "not_applicable"]),
        present_in_final_output=_enum("", ["yes", "no", "partial", "unclear", "not_applicable"]),
        objective_relevance=_enum("", ["irrelevant", "peripheral", "subtask_relevant", "main_objective_relevant", "final_output_relevant"]),
        severity=_enum("", ["minor", "moderate", "major", "critical", "unclear"]),
        outcome_link=_enum("", ["none", "unlikely", "possible", "likely", "direct", "unclear"]),
        candidate_attribution=_enum("", ["none", "low", "medium", "high", "unclear"]),
        external_attribution=_enum("", ["none", "low", "medium", "high", "unclear"]),
        actionability=_enum("", ["none", "low", "medium", "high", "very_high", "unclear"]),
    )


def mapping_user_prompt(failure_points: list, taxonomy_catalog: str) -> str:
    """Render the mapping user prompt given Stage-1 failure points + taxonomy text."""
    return Template(_asset("mapping_user.md")).substitute(
        failure_points=json.dumps(failure_points, indent=2, ensure_ascii=False),
        taxonomy_catalog=taxonomy_catalog,
    )


def single_call_user_prompt(judge_input: Mapping[str, Any], taxonomy_catalog: str) -> str:
    """One-shot version: analysis + mapping in one call (cost-sensitive use)."""
    return Template(_asset("single_call_user.md")).substitute(
        analysis_part=analysis_user_prompt(judge_input),
        taxonomy_catalog=taxonomy_catalog,
    )


def retry_user_prompt(previous_output_text: str, validation_errors: list[str]) -> str:
    """Ask the judge to repair output that failed schema validation."""
    return Template(_asset("retry_user.md")).substitute(
        validation_errors="\n".join(f"  - {e}" for e in validation_errors),
        previous_output_text=previous_output_text,
    )
