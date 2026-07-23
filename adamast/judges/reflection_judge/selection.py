"""Deterministic derivation of ``selection_summary`` from failure points + relations.

Pure stdlib; no provider-specific imports.

The judge's LLM output contains the rich diagnostic graph. The
``selection_summary`` is a compressed, set-oriented view of that graph used by
search or optimization algorithms. We derive it
deterministically in Python so the rules are auditable and don't depend on the
LLM remembering them.

Rules:
  - strong mapping (conf >= strong_threshold)  -> normal use; refiner OK
  - weak mapping   (conf <  weak_threshold)    -> weak_taxonomy_matches
                                                  (refiner: edit / split)
  - unmapped (proposed_failure_mode)           -> unmapped_failure_points
                                                  (refiner: add)

isolated_terminal_root folds into root_failure_modes (high value).
isolated_irrelevant goes into isolated_failure_modes (informational only).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


def _codes_of(fp: Mapping[str, Any]) -> list[str]:
    """All taxonomy codes assigned to a failure point (primary + secondary)."""
    return [m.get("code") for m in (fp.get("taxonomy_mappings") or []) if m.get("code")]


def _unique_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def derive_selection_summary(
    failure_points: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]] | None = None,
    *,
    weak_threshold: float = 0.5,
    strong_threshold: float = 0.7,
) -> dict:
    """Compute the compressed selection summary from failure points (and relations)."""
    _ = strong_threshold  # reserved
    rels = list(relations or [])
    _ = rels  # reserved

    root_codes: list[str] = []
    candidate_codes: list[str] = []
    external_codes: list[str] = []
    unrecovered_codes: list[str] = []
    recovered_codes: list[str] = []
    terminal_codes: list[str] = []
    isolated_codes: list[str] = []
    actionable_codes: list[str] = []
    high_sev_codes: list[str] = []
    outcome_codes: list[str] = []

    unmapped_fp_ids: list[dict] = []
    weak_matches: list[dict] = []

    for fp in failure_points or []:
        codes = _codes_of(fp)
        role = fp.get("causal_role")
        if role in ("root_cause", "isolated_terminal_root"):
            root_codes.extend(codes)
        if role == "external_condition":
            external_codes.extend(codes)
        if role == "terminal_symptom":
            terminal_codes.extend(codes)
        if role == "isolated_irrelevant":
            isolated_codes.extend(codes)

        if fp.get("candidate_attribution") in ("medium", "high"):
            candidate_codes.extend(codes)
        if fp.get("external_attribution") in ("medium", "high"):
            external_codes.extend(codes)

        if fp.get("recovery_status") == "unrecovered":
            unrecovered_codes.extend(codes)
        if fp.get("recovery_status") in ("fully_recovered", "partially_recovered"):
            recovered_codes.extend(codes)

        if fp.get("actionability") in ("high", "very_high"):
            actionable_codes.extend(codes)
        if fp.get("severity") in ("major", "critical"):
            high_sev_codes.extend(codes)
        if fp.get("outcome_link") in ("likely", "direct"):
            outcome_codes.extend(codes)

        if fp.get("unmapped"):
            proposed = fp.get("proposed_failure_mode") or {}
            unmapped_fp_ids.append({
                "failure_point_id": fp.get("failure_point_id"),
                "summary": fp.get("summary"),
                "proposed_name": proposed.get("name"),
                "proposed_definition": proposed.get("definition"),
                "ruled_out_codes": [
                    r.get("code") for r in (fp.get("ruled_out_codes") or [])
                ],
            })
        for m in (fp.get("taxonomy_mappings") or []):
            conf = m.get("mapping_confidence")
            if isinstance(conf, (int, float)) and float(conf) < weak_threshold:
                weak_matches.append({
                    "failure_point_id": fp.get("failure_point_id"),
                    "code": m.get("code"),
                    "name": m.get("name"),
                    "mapping_confidence": float(conf),
                    "mapping_rationale": m.get("mapping_rationale"),
                })

    return {
        "root_failure_modes": _unique_preserve_order(root_codes),
        "candidate_attributable_failure_modes": _unique_preserve_order(candidate_codes),
        "external_or_environmental_failure_modes": _unique_preserve_order(external_codes),
        "unrecovered_failure_modes": _unique_preserve_order(unrecovered_codes),
        "recovered_failure_modes": _unique_preserve_order(recovered_codes),
        "terminal_symptom_modes": _unique_preserve_order(terminal_codes),
        "isolated_failure_modes": _unique_preserve_order(isolated_codes),
        "actionable_failure_modes": _unique_preserve_order(actionable_codes),
        "high_severity_failure_modes": _unique_preserve_order(high_sev_codes),
        "outcome_linked_failure_modes": _unique_preserve_order(outcome_codes),
        "unmapped_failure_points": unmapped_fp_ids,
        "weak_taxonomy_matches": weak_matches,
    }
