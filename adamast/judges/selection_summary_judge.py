"""Standalone deterministic Selection-Summary judge wrapper.

This module exposes the Reflection Judge's selection-summary derivation as a
public judge entrypoint without re-running an LLM.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .reflection_judge.selection import derive_selection_summary


def run(
    failure_points: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]] | None = None,
    *,
    weak_threshold: float = 0.5,
    strong_threshold: float = 0.7,
) -> dict:
    """Compute the compressed selection summary deterministically.

    Parameters
    ----------
    failure_points
        Per-failure-point dicts in the Reflection Judge output shape
        (must contain ``taxonomy_mappings``, ``causal_role``,
        ``recovery_status``, ``actionability``, etc.).
    relations
        Optional list of relation dicts (currently unused but reserved for
        future graph-based summaries).
    weak_threshold
        Mappings with ``mapping_confidence`` below this go into
        ``weak_taxonomy_matches``.
    strong_threshold
        Reserved for future tiering.

    Returns
    -------
    dict
        Selection-summary buckets (see module docstring).
    """
    return derive_selection_summary(
        failure_points,
        relations,
        weak_threshold=weak_threshold,
        strong_threshold=strong_threshold,
    )


__all__ = ["run"]
