"""Schema for the AdaMAST Reflection Judge output.

Pure stdlib; no provider-specific imports.

The output is a plain ``dict`` (JSON-roundtrippable). This module defines the
enumerated values each field may take and a ``validate_output`` function that
returns a list of human-readable error strings (empty list = valid). The
validator runs on every LLM response; invalid responses trigger a one-shot
retry, surfacing the validator's errors back to the LLM as the retry context.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Mapping


_SCHEMA_ENUMS = json.loads(
    files(__package__).joinpath("assets").joinpath("schema_enums.json").read_text(
        encoding="utf-8"
    )
)


def _enum_set(name: str) -> set[str]:
    return set(_SCHEMA_ENUMS[name])


REQUIRED_TOP_LEVEL = _enum_set("required_top_level")
REQUIRED_EVENT_FIELDS = _enum_set("required_event_fields")
SUGGESTED_EVENT_STAGES = _enum_set("suggested_event_stages")
REQUIRED_FAILURE_POINT_FIELDS = _enum_set("required_failure_point_fields")
REASON_OBSERVED_INFERRED = _enum_set("reason_observed_inferred")
EVIDENCE_STRENGTHS = _enum_set("evidence_strengths")
CAUSAL_ROLES = _enum_set("causal_roles")
RECOVERY_STATUSES = _enum_set("recovery_statuses")
RECOVERY_SOURCES = _enum_set("recovery_sources")
PRESENT_IN_FINAL = _enum_set("present_in_final")
OBJECTIVE_RELEVANCE = _enum_set("objective_relevance")
SEVERITIES = _enum_set("severities")
OUTCOME_LINKS = _enum_set("outcome_links")
ATTRIBUTIONS = _enum_set("attributions")
ACTIONABILITIES = _enum_set("actionabilities")
REQUIRED_MAPPING_FIELDS = _enum_set("required_mapping_fields")
PRIMARY_OR_SECONDARY = _enum_set("primary_or_secondary")
REQUIRED_RELATION_FIELDS = _enum_set("required_relation_fields")
RELATION_TYPES = _enum_set("relation_types")
OVERALL_JUDGMENTS = _enum_set("overall_judgments")


def _err(errs: list, where: str, msg: str) -> None:
    errs.append(f"[{where}] {msg}")


def _check_in(errs: list, where: str, field: str, value: Any, allowed: set) -> None:
    if value not in allowed:
        _err(errs, where, f"{field}={value!r} not in {sorted(allowed)}")


def _check_float_0_1(errs: list, where: str, field: str, value: Any) -> None:
    if not isinstance(value, (int, float)) or not (0.0 <= float(value) <= 1.0):
        _err(errs, where, f"{field}={value!r} must be a float in [0.0, 1.0]")


def validate_output(output: Mapping[str, Any]) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    errs: list[str] = []
    if not isinstance(output, Mapping):
        return ["[root] output must be a JSON object"]

    missing = REQUIRED_TOP_LEVEL - set(output.keys())
    if missing:
        _err(errs, "root", f"missing required keys: {sorted(missing)}")

    ts = output.get("trace_summary") or {}
    if isinstance(ts, Mapping):
        oj = ts.get("overall_judgment")
        if oj is not None:
            _check_in(errs, "trace_summary", "overall_judgment", oj, OVERALL_JUDGMENTS)

    events = output.get("events") or []
    event_ids: set[str] = set()
    if not isinstance(events, list):
        _err(errs, "events", "must be a list")
    else:
        for i, e in enumerate(events):
            where = f"events[{i}]"
            if not isinstance(e, Mapping):
                _err(errs, where, "must be an object")
                continue
            missing = REQUIRED_EVENT_FIELDS - set(e.keys())
            if missing:
                _err(errs, where, f"missing fields: {sorted(missing)}")
            eid = e.get("event_id")
            if eid in event_ids:
                _err(errs, where, f"duplicate event_id: {eid!r}")
            if isinstance(eid, str):
                event_ids.add(eid)
            stage = e.get("stage")
            if stage is not None and not (isinstance(stage, str) and stage.strip()):
                _err(errs, where, f"stage must be a non-empty string when present (got {stage!r})")

    fps = output.get("failure_points") or []
    fp_ids: set[str] = set()
    if not isinstance(fps, list):
        _err(errs, "failure_points", "must be a list")
    else:
        for i, fp in enumerate(fps):
            where = f"failure_points[{i}]"
            if not isinstance(fp, Mapping):
                _err(errs, where, "must be an object")
                continue
            missing = REQUIRED_FAILURE_POINT_FIELDS - set(fp.keys())
            if missing:
                _err(errs, where, f"missing fields: {sorted(missing)}")

            fpid = fp.get("failure_point_id")
            if fpid in fp_ids:
                _err(errs, where, f"duplicate failure_point_id: {fpid!r}")
            if isinstance(fpid, str):
                fp_ids.add(fpid)

            _check_in(
                errs,
                where,
                "reason_observed_or_inferred",
                fp.get("reason_observed_or_inferred"),
                REASON_OBSERVED_INFERRED,
            )
            _check_in(errs, where, "evidence_strength", fp.get("evidence_strength"), EVIDENCE_STRENGTHS)
            _check_float_0_1(errs, where, "judge_confidence", fp.get("judge_confidence"))
            _check_in(errs, where, "causal_role", fp.get("causal_role"), CAUSAL_ROLES)
            _check_in(errs, where, "recovery_status", fp.get("recovery_status"), RECOVERY_STATUSES)
            if "recovery_source" in fp:
                _check_in(errs, where, "recovery_source", fp["recovery_source"], RECOVERY_SOURCES)
            _check_in(
                errs,
                where,
                "present_in_final_output",
                fp.get("present_in_final_output"),
                PRESENT_IN_FINAL,
            )
            _check_in(errs, where, "objective_relevance", fp.get("objective_relevance"), OBJECTIVE_RELEVANCE)
            _check_in(errs, where, "severity", fp.get("severity"), SEVERITIES)
            _check_in(errs, where, "outcome_link", fp.get("outcome_link"), OUTCOME_LINKS)
            _check_in(errs, where, "candidate_attribution", fp.get("candidate_attribution"), ATTRIBUTIONS)
            _check_in(errs, where, "external_attribution", fp.get("external_attribution"), ATTRIBUTIONS)
            _check_in(errs, where, "actionability", fp.get("actionability"), ACTIONABILITIES)

            for j, ev_id in enumerate(fp.get("event_ids") or []):
                if ev_id not in event_ids:
                    _err(errs, where, f"event_ids[{j}]={ev_id!r} not found in events")

            evidence = fp.get("observed_evidence")
            if not isinstance(evidence, str) or not evidence.strip():
                _err(errs, where, "observed_evidence must be a non-empty string")

            if fp.get("causal_role") == "isolated_terminal_root":
                rationale = fp.get("downstream_clean_rationale") or fp.get("inferred_mechanism")
                if not isinstance(rationale, str) or not rationale.strip():
                    _err(
                        errs,
                        where,
                        "isolated_terminal_root requires 'downstream_clean_rationale' "
                        "(or 'inferred_mechanism') explaining why the trace looked "
                        "clean after this failure",
                    )

            mappings = fp.get("taxonomy_mappings") or []
            unmapped = bool(fp.get("unmapped", False))
            if not isinstance(mappings, list):
                _err(errs, where, "taxonomy_mappings must be a list")
            else:
                for j, m in enumerate(mappings):
                    mwhere = f"{where}.taxonomy_mappings[{j}]"
                    if not isinstance(m, Mapping):
                        _err(errs, mwhere, "must be an object")
                        continue
                    miss = REQUIRED_MAPPING_FIELDS - set(m.keys())
                    if miss:
                        _err(errs, mwhere, f"missing fields: {sorted(miss)}")
                    _check_in(
                        errs,
                        mwhere,
                        "primary_or_secondary",
                        m.get("primary_or_secondary"),
                        PRIMARY_OR_SECONDARY,
                    )
                    _check_float_0_1(errs, mwhere, "mapping_confidence", m.get("mapping_confidence"))

            if unmapped:
                ruled = fp.get("ruled_out_codes") or []
                if not isinstance(ruled, list) or len(ruled) < 1:
                    _err(
                        errs,
                        where,
                        "unmapped=true requires a non-empty 'ruled_out_codes' "
                        "list (each entry: {code, reason}) showing the closest "
                        "existing codes considered and why they do not fit",
                    )
                else:
                    for j, r in enumerate(ruled):
                        if not isinstance(r, Mapping) or not r.get("code") or not r.get("reason"):
                            _err(errs, f"{where}.ruled_out_codes[{j}]", "must be {code: str, reason: str}")
                proposed = fp.get("proposed_failure_mode") or {}
                if (
                    not isinstance(proposed, Mapping)
                    or not proposed.get("name")
                    or not proposed.get("definition")
                ):
                    _err(
                        errs,
                        where,
                        "unmapped=true requires 'proposed_failure_mode': "
                        "{name, definition, detection_heuristics?}",
                    )

    rels = output.get("relations") or []
    if not isinstance(rels, list):
        _err(errs, "relations", "must be a list")
    else:
        for i, r in enumerate(rels):
            where = f"relations[{i}]"
            if not isinstance(r, Mapping):
                _err(errs, where, "must be an object")
                continue
            miss = REQUIRED_RELATION_FIELDS - set(r.keys())
            if miss:
                _err(errs, where, f"missing fields: {sorted(miss)}")
            _check_in(errs, where, "relation_type", r.get("relation_type"), RELATION_TYPES)
            _check_float_0_1(errs, where, "confidence", r.get("confidence"))
            for end in ("source_failure_point_id", "target_failure_point_id"):
                fid = r.get(end)
                if fid not in fp_ids:
                    _err(errs, where, f"{end}={fid!r} not found in failure_points")

    return errs
