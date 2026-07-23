"""Shared controller for AdaMAST's simple LLM judge types.

The behavior-defining judge instructions live in ``adamast/judges/assets/*/*.md``.
This module owns only orchestration, prompt rendering, output validation,
salvage, and typed compatibility wrappers.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from importlib import resources
from string import Template
from typing import Any, Callable, Mapping, Sequence

from adamast.llm.learning_calls import judge_json
from adamast.core.taxonomy_data import Taxonomy

JudgeCallable = Callable[[str, str], str | None]

SIMPLE_JUDGE_TYPES = (
    "selection",
    "mapping",
    "coverage",
    "quality",
    "calibration",
)

CONFIDENCE_TIERS = {"low", "medium", "high"}
SEVERITY_TIERS = {"minor", "moderate", "major", "critical"}
COVERAGE_STATUSES = {"covered", "partially_covered", "not_covered"}
OVERALL_QUALITIES = {"good", "needs_refinement", "poor"}
EVIDENCE_SUPPORT = {"strong", "moderate", "weak", "none"}


def load_judge_definition(judge_type: str) -> dict[str, str]:
    """Return the natural-language system and user templates for a judge."""
    _ensure_known(judge_type)
    base = resources.files("adamast.judges").joinpath("assets").joinpath(judge_type)
    return {
        "system": base.joinpath("system.md").read_text(encoding="utf-8"),
        "user_template": base.joinpath("user.md").read_text(encoding="utf-8"),
    }


def render_judge_prompt(judge_type: str, **context: Any) -> tuple[str, str]:
    """Render ``(system, user)`` for a natural-language judge asset."""
    definition = load_judge_definition(judge_type)
    user = Template(definition["user_template"]).substitute(
        {key: _stringify(value) for key, value in context.items()}
    )
    return definition["system"], user


def _ensure_known(judge_type: str) -> None:
    if judge_type not in SIMPLE_JUDGE_TYPES:
        raise ValueError(f"unknown simple judge type: {judge_type!r}")


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    return json.dumps(value, indent=2, ensure_ascii=False)


@dataclass(frozen=True)
class SelectionJudgeResult:
    failure_modes: list[dict[str, Any]] = field(default_factory=list)
    none_apply: bool = False
    judge_metadata: dict[str, Any] = field(default_factory=dict)

    def code_ids(self) -> list[str]:
        return [m["code"] for m in self.failure_modes if m.get("code")]


@dataclass(frozen=True)
class MappingJudgeResult:
    primary_code: str | None = None
    secondary_codes: list[str] = field(default_factory=list)
    mapping_confidence: float = 0.0
    unmapped: bool = False
    proposed_failure_mode: dict[str, Any] | None = None
    ruled_out_codes: list[dict[str, str]] = field(default_factory=list)
    judge_metadata: dict[str, Any] = field(default_factory=dict)

    def all_codes(self) -> list[str]:
        if not self.primary_code:
            return list(self.secondary_codes)
        return [self.primary_code, *self.secondary_codes]


@dataclass(frozen=True)
class CoverageJudgeResult:
    coverage_status: str = "not_covered"
    closest_codes: list[str] = field(default_factory=list)
    missing_failure_pattern: str | None = None
    suggest_new_code: bool = False
    proposed_failure_mode: dict[str, Any] | None = None
    judge_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityJudgeResult:
    code_quality: list[dict[str, Any]] = field(default_factory=list)
    overall_quality: str = "needs_refinement"
    overall_summary: str = ""
    judge_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationJudgeResult:
    annotation_valid: bool = False
    evidence_support: str = "none"
    possible_overtrigger: bool = False
    conflicting_codes: list[str] = field(default_factory=list)
    rationale: str = ""
    judge_metadata: dict[str, Any] = field(default_factory=dict)


class JudgeController:
    """General Python controller for natural-language simple judge assets."""

    def __init__(
        self,
        judge_type: str,
        taxonomy: Taxonomy,
        *,
        judge_model: str,
        llm_call: JudgeCallable | None = None,
        max_retries: int = 1,
    ):
        _ensure_known(judge_type)
        if not judge_model:
            raise ValueError("judge_model is required")
        self.judge_type = judge_type
        self.taxonomy = taxonomy
        self.judge_model = judge_model
        self.llm_call = llm_call
        self.max_retries = max_retries
        self._catalog = taxonomy.prompt_block()
        self._catalog_codes = {c.code for c in taxonomy.codes}

    def run(self, payload: Any = None) -> Any:
        if self.judge_type == "selection":
            return self._run_selection(str(payload or ""))
        if self.judge_type == "mapping":
            return self._run_mapping(payload)
        if self.judge_type == "coverage":
            return self._run_coverage(payload)
        if self.judge_type == "quality":
            return self._run_quality(payload)
        if self.judge_type == "calibration":
            return self._run_calibration(payload)
        raise AssertionError(f"unhandled judge type: {self.judge_type}")

    def _call(self, system: str, user: str, warnings: list[str]) -> Mapping[str, Any]:
        combined = f"{system}\n\n{user}"
        raw = judge_json(
            combined,
            self.judge_model,
            max_retries=self.max_retries,
            call=self.llm_call,
        )
        if raw is None:
            warnings.append("judge_json returned None (LLM call failed or invalid JSON)")
            return {}
        return raw

    def _metadata(self, warnings: list[str]) -> dict[str, Any]:
        return {
            "judge": self.judge_type,
            "judge_model": self.judge_model,
            "taxonomy_version": self.taxonomy.version,
            "created_at": int(time.time()),
            "warnings": list(warnings),
        }

    def _run_selection(self, trace_text: str) -> SelectionJudgeResult:
        system, user = render_judge_prompt(
            "selection",
            taxonomy_catalog=self._catalog,
            trace_text=trace_text,
        )
        warnings: list[str] = []
        raw = self._call(system, user, warnings)
        if not raw:
            return SelectionJudgeResult(judge_metadata=self._metadata(warnings))
        errs = validate_selection_output(raw, self._catalog_codes)
        if errs:
            warnings.extend(errs[:5])
            modes = [
                dict(m) for m in (raw.get("failure_modes") or [])
                if (
                    isinstance(m, Mapping)
                    and isinstance(m.get("code"), str)
                    and m.get("code") in self._catalog_codes
                    and isinstance(m.get("evidence"), str)
                    and m["evidence"].strip()
                )
            ]
        else:
            modes = [dict(m) for m in (raw.get("failure_modes") or [])]
        none_apply = bool(raw.get("none_apply", False)) and not modes
        if not modes and not none_apply:
            warnings.append(
                "judge returned no failure_modes and did not set none_apply=true"
            )
        return SelectionJudgeResult(
            failure_modes=modes,
            none_apply=none_apply,
            judge_metadata=self._metadata(warnings),
        )

    def _run_mapping(self, failure_point: Any) -> MappingJudgeResult:
        if not isinstance(failure_point, Mapping):
            raise TypeError("failure_point must be a mapping (dict)")
        system, user = render_judge_prompt(
            "mapping",
            failure_point=dict(failure_point),
            taxonomy_catalog=self._catalog,
        )
        warnings: list[str] = []
        raw = self._call(system, user, warnings)
        if not raw:
            return MappingJudgeResult(judge_metadata=self._metadata(warnings))
        errs = validate_mapping_output(raw, self._catalog_codes)
        if errs:
            warnings.extend(errs[:5])
        unmapped = bool(raw.get("unmapped", False))
        primary = raw.get("primary_code") if not unmapped else None
        if primary is not None and not (
            isinstance(primary, str) and primary in self._catalog_codes
        ):
            primary = None
        secondary = [
            s for s in (raw.get("secondary_codes") or [])
            if isinstance(s, str) and s in self._catalog_codes and s != primary
        ]
        conf = raw.get("mapping_confidence", 0.0)
        if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
            conf = 0.0
        proposed = raw.get("proposed_failure_mode")
        if not (isinstance(proposed, Mapping) and proposed.get("name") and proposed.get("definition")):
            proposed = None
        ruled = [
            {"code": str(r["code"]), "reason": str(r["reason"])}
            for r in (raw.get("ruled_out_codes") or [])
            if isinstance(r, Mapping) and r.get("code") and r.get("reason")
        ]
        return MappingJudgeResult(
            primary_code=primary,
            secondary_codes=secondary,
            mapping_confidence=float(conf),
            unmapped=unmapped,
            proposed_failure_mode=dict(proposed) if proposed else None,
            ruled_out_codes=ruled,
            judge_metadata=self._metadata(warnings),
        )

    def _run_coverage(self, payload: Any) -> CoverageJudgeResult:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping (dict)")
        if not payload.get("trace") and not isinstance(payload.get("failure_point"), Mapping):
            raise ValueError(
                "payload must contain at least one of: trace (str) or "
                "failure_point (dict)"
            )
        failure_point = (
            dict(payload["failure_point"])
            if isinstance(payload.get("failure_point"), Mapping)
            else "(not provided)"
        )
        system, user = render_judge_prompt(
            "coverage",
            taxonomy_catalog=self._catalog,
            failure_point=failure_point,
            trace_text=str(payload.get("trace") or "").strip() or "(not provided)",
        )
        warnings: list[str] = []
        raw = self._call(system, user, warnings)
        if not raw:
            return CoverageJudgeResult(judge_metadata=self._metadata(warnings))
        errs = validate_coverage_output(raw, self._catalog_codes)
        if errs:
            warnings.extend(errs[:5])
        status = raw.get("coverage_status")
        if status not in COVERAGE_STATUSES:
            status = "not_covered"
        closest = [
            c for c in (raw.get("closest_codes") or [])
            if isinstance(c, str) and c in self._catalog_codes
        ]
        missing = raw.get("missing_failure_pattern")
        if not isinstance(missing, str):
            missing = None
        suggest = bool(raw.get("suggest_new_code", False))
        proposed = raw.get("proposed_failure_mode")
        if not (isinstance(proposed, Mapping) and proposed.get("name") and proposed.get("definition")):
            proposed = None
            if suggest:
                suggest = False
        return CoverageJudgeResult(
            coverage_status=status,
            closest_codes=closest,
            missing_failure_pattern=missing,
            suggest_new_code=suggest,
            proposed_failure_mode=dict(proposed) if proposed else None,
            judge_metadata=self._metadata(warnings),
        )

    def _run_quality(self, support_traces: Any = None) -> QualityJudgeResult:
        traces = support_traces if isinstance(support_traces, Sequence) and not isinstance(support_traces, str) else None
        if traces:
            traces_block = "\n\n---\n\n".join(
                f"### support trace {i + 1}\n{trace}"
                for i, trace in enumerate(traces)
            )
            traces_section = f"## SUPPORT TRACES\n{traces_block}\n\n"
        else:
            traces_section = (
                "## SUPPORT TRACES\n"
                "(none provided; evaluate on definitional grounds alone)\n\n"
            )
        system, user = render_judge_prompt(
            "quality",
            taxonomy_catalog=self._catalog,
            support_traces_section=traces_section,
        )
        warnings: list[str] = []
        raw = self._call(system, user, warnings)
        if not raw:
            return QualityJudgeResult(judge_metadata=self._metadata(warnings))
        errs = validate_quality_output(raw, self._catalog_codes)
        if errs:
            warnings.extend(errs[:5])
        kept: list[dict[str, Any]] = []
        for item in (raw.get("code_quality") or []):
            if not isinstance(item, Mapping):
                continue
            code = item.get("code")
            if not (isinstance(code, str) and code in self._catalog_codes):
                continue
            if not (
                isinstance(item.get("issue"), str)
                and item["issue"].strip()
                and isinstance(item.get("recommendation"), str)
                and item["recommendation"].strip()
            ):
                continue
            kept.append(dict(item))
        overall = raw.get("overall_quality")
        if overall not in OVERALL_QUALITIES:
            overall = "needs_refinement"
        summary = raw.get("overall_summary")
        if not isinstance(summary, str):
            summary = ""
        return QualityJudgeResult(
            code_quality=kept,
            overall_quality=overall,
            overall_summary=summary,
            judge_metadata=self._metadata(warnings),
        )

    def _run_calibration(self, payload: Any) -> CalibrationJudgeResult:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping (dict)")
        annotation = payload.get("annotation")
        if not isinstance(annotation, Mapping) or not annotation.get("code"):
            raise ValueError(
                "payload.annotation must be a dict with at least a 'code' field"
            )
        evidence = payload.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("payload.evidence must be a non-empty string")
        annotated_code = annotation["code"]
        if annotated_code not in self._catalog_codes:
            raise ValueError(
                f"annotation.code={annotated_code!r} not in taxonomy catalog "
                f"(known codes: {sorted(self._catalog_codes)})"
            )
        system, user = render_judge_prompt(
            "calibration",
            taxonomy_catalog=self._catalog,
            annotation=dict(annotation),
            evidence=evidence,
        )
        warnings: list[str] = []
        raw = self._call(system, user, warnings)
        if not raw:
            return CalibrationJudgeResult(judge_metadata=self._metadata(warnings))
        errs = validate_calibration_output(raw, self._catalog_codes)
        if errs:
            warnings.extend(errs[:5])
        support = raw.get("evidence_support")
        if support not in EVIDENCE_SUPPORT:
            support = "none"
        valid = bool(raw.get("annotation_valid", False))
        if valid and support not in ("strong", "moderate"):
            valid = False
            warnings.append(
                f"forcing annotation_valid=false (evidence_support={support!r})"
            )
        overtrigger = bool(raw.get("possible_overtrigger", False))
        conflicting = [
            c for c in (raw.get("conflicting_codes") or [])
            if isinstance(c, str) and c in self._catalog_codes and c != annotated_code
        ]
        rationale = raw.get("rationale")
        if not isinstance(rationale, str):
            rationale = ""
        return CalibrationJudgeResult(
            annotation_valid=valid,
            evidence_support=support,
            possible_overtrigger=overtrigger,
            conflicting_codes=conflicting,
            rationale=rationale,
            judge_metadata=self._metadata(warnings),
        )


class SelectionJudge:
    def __init__(self, taxonomy: Taxonomy, *, judge_model: str,
                 llm_call: JudgeCallable | None = None, max_retries: int = 1):
        self._controller = JudgeController(
            "selection", taxonomy, judge_model=judge_model,
            llm_call=llm_call, max_retries=max_retries,
        )

    def run(self, trace_text: str) -> SelectionJudgeResult:
        return self._controller.run(trace_text)

    def run_many(self, trace_texts: Sequence[str]) -> list[SelectionJudgeResult]:
        return [self.run(t) for t in trace_texts]


class MappingJudge:
    def __init__(self, taxonomy: Taxonomy, *, judge_model: str,
                 llm_call: JudgeCallable | None = None, max_retries: int = 1):
        self._controller = JudgeController(
            "mapping", taxonomy, judge_model=judge_model,
            llm_call=llm_call, max_retries=max_retries,
        )

    def run(self, failure_point: Mapping[str, Any]) -> MappingJudgeResult:
        return self._controller.run(failure_point)


class CoverageJudge:
    def __init__(self, taxonomy: Taxonomy, *, judge_model: str,
                 llm_call: JudgeCallable | None = None, max_retries: int = 1):
        self._controller = JudgeController(
            "coverage", taxonomy, judge_model=judge_model,
            llm_call=llm_call, max_retries=max_retries,
        )

    def run(self, payload: Mapping[str, Any]) -> CoverageJudgeResult:
        return self._controller.run(payload)


class QualityJudge:
    def __init__(self, taxonomy: Taxonomy, *, judge_model: str,
                 llm_call: JudgeCallable | None = None, max_retries: int = 1):
        self._controller = JudgeController(
            "quality", taxonomy, judge_model=judge_model,
            llm_call=llm_call, max_retries=max_retries,
        )

    def run(self, support_traces: Sequence[str] | None = None) -> QualityJudgeResult:
        return self._controller.run(support_traces)


class CalibrationJudge:
    def __init__(self, taxonomy: Taxonomy, *, judge_model: str,
                 llm_call: JudgeCallable | None = None, max_retries: int = 1):
        self._controller = JudgeController(
            "calibration", taxonomy, judge_model=judge_model,
            llm_call=llm_call, max_retries=max_retries,
        )

    def run(self, payload: Mapping[str, Any]) -> CalibrationJudgeResult:
        return self._controller.run(payload)


def validate_selection_output(data: Mapping[str, Any], catalog_codes: set[str]) -> list[str]:
    errs: list[str] = []
    if not isinstance(data, Mapping):
        return ["root: output must be a JSON object"]
    modes = data.get("failure_modes")
    none_apply = bool(data.get("none_apply", False))
    if not isinstance(modes, list):
        errs.append("failure_modes: must be a list")
        modes = []
    if modes and none_apply:
        errs.append("none_apply=true requires failure_modes=[]")
    seen_codes: set[str] = set()
    for i, item in enumerate(modes):
        where = f"failure_modes[{i}]"
        if not isinstance(item, Mapping):
            errs.append(f"{where}: must be an object")
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            errs.append(f"{where}.code: must be a non-empty string")
            continue
        if catalog_codes and code not in catalog_codes:
            errs.append(f"{where}.code: {code!r} not in taxonomy catalog")
        if code in seen_codes:
            errs.append(f"{where}.code: duplicate {code!r}")
        seen_codes.add(code)
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            errs.append(f"{where}.evidence: must be a non-empty string")
        conf = item.get("confidence")
        if conf not in CONFIDENCE_TIERS:
            errs.append(f"{where}.confidence: {conf!r} not in {sorted(CONFIDENCE_TIERS)}")
        sev = item.get("severity")
        if sev not in SEVERITY_TIERS:
            errs.append(f"{where}.severity: {sev!r} not in {sorted(SEVERITY_TIERS)}")
    return errs


def validate_mapping_output(data: Mapping[str, Any], catalog_codes: set[str]) -> list[str]:
    errs: list[str] = []
    if not isinstance(data, Mapping):
        return ["root: output must be a JSON object"]
    unmapped = bool(data.get("unmapped", False))
    primary = data.get("primary_code")
    secondary = data.get("secondary_codes") or []
    proposed = data.get("proposed_failure_mode")
    ruled_out = data.get("ruled_out_codes") or []
    conf = data.get("mapping_confidence")
    if not isinstance(secondary, list):
        errs.append("secondary_codes: must be a list")
        secondary = []
    if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
        errs.append(f"mapping_confidence: {conf!r} must be a float in [0.0, 1.0]")
    if unmapped:
        if primary is not None:
            errs.append("unmapped=true: primary_code must be null")
        if secondary:
            errs.append("unmapped=true: secondary_codes must be []")
        if not isinstance(proposed, Mapping) or not proposed.get("name") or not proposed.get("definition"):
            errs.append("unmapped=true requires proposed_failure_mode: {name, definition, detection_heuristics?}")
        if not isinstance(ruled_out, list) or len(ruled_out) < 1:
            errs.append("unmapped=true requires non-empty ruled_out_codes (each entry: {code, reason})")
        else:
            for i, item in enumerate(ruled_out):
                if not isinstance(item, Mapping) or not item.get("code") or not item.get("reason"):
                    errs.append(f"ruled_out_codes[{i}]: must be {{code, reason}} with both set")
    else:
        if not isinstance(primary, str) or not primary.strip():
            errs.append("unmapped=false: primary_code must be a non-empty string")
        elif catalog_codes and primary not in catalog_codes:
            errs.append(f"primary_code: {primary!r} not in taxonomy catalog")
        for i, code in enumerate(secondary):
            if not isinstance(code, str):
                errs.append(f"secondary_codes[{i}]: must be a string")
            elif catalog_codes and code not in catalog_codes:
                errs.append(f"secondary_codes[{i}]: {code!r} not in taxonomy catalog")
            elif code == primary:
                errs.append(f"secondary_codes[{i}]: {code!r} is the primary code")
    return errs


def validate_coverage_output(data: Mapping[str, Any], catalog_codes: set[str]) -> list[str]:
    errs: list[str] = []
    if not isinstance(data, Mapping):
        return ["root: output must be a JSON object"]
    status = data.get("coverage_status")
    if status not in COVERAGE_STATUSES:
        errs.append(f"coverage_status: {status!r} not in {sorted(COVERAGE_STATUSES)}")
    closest = data.get("closest_codes") or []
    if not isinstance(closest, list):
        errs.append("closest_codes: must be a list")
        closest = []
    for i, code in enumerate(closest):
        if not isinstance(code, str):
            errs.append(f"closest_codes[{i}]: must be a string")
        elif catalog_codes and code not in catalog_codes:
            errs.append(f"closest_codes[{i}]: {code!r} not in taxonomy catalog")
    suggest = bool(data.get("suggest_new_code", False))
    proposed = data.get("proposed_failure_mode")
    missing = data.get("missing_failure_pattern")
    if status == "covered":
        if missing not in (None, ""):
            errs.append("coverage_status=covered: missing_failure_pattern must be null")
        if suggest:
            errs.append("coverage_status=covered: suggest_new_code must be false")
    if suggest and not (
        isinstance(proposed, Mapping) and proposed.get("name") and proposed.get("definition")
    ):
        errs.append("suggest_new_code=true requires proposed_failure_mode {name, definition, detection_heuristics?}")
    return errs


def validate_quality_output(data: Mapping[str, Any], catalog_codes: set[str]) -> list[str]:
    errs: list[str] = []
    if not isinstance(data, Mapping):
        return ["root: output must be a JSON object"]
    code_quality = data.get("code_quality") or []
    if not isinstance(code_quality, list):
        errs.append("code_quality: must be a list")
        code_quality = []
    seen = set()
    for i, item in enumerate(code_quality):
        where = f"code_quality[{i}]"
        if not isinstance(item, Mapping):
            errs.append(f"{where}: must be an object")
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            errs.append(f"{where}.code: must be a non-empty string")
            continue
        if catalog_codes and code not in catalog_codes:
            errs.append(f"{where}.code: {code!r} not in taxonomy catalog")
        if code in seen:
            errs.append(f"{where}.code: duplicate {code!r}")
        seen.add(code)
        for required in ("issue", "recommendation"):
            value = item.get(required)
            if not isinstance(value, str) or not value.strip():
                errs.append(f"{where}.{required}: must be a non-empty string")
    overall = data.get("overall_quality")
    if overall not in OVERALL_QUALITIES:
        errs.append(f"overall_quality: {overall!r} not in {sorted(OVERALL_QUALITIES)}")
    summary = data.get("overall_summary")
    if not isinstance(summary, str) or not summary.strip():
        errs.append("overall_summary: must be a non-empty string")
    return errs


def validate_calibration_output(data: Mapping[str, Any], catalog_codes: set[str]) -> list[str]:
    errs: list[str] = []
    if not isinstance(data, Mapping):
        return ["root: output must be a JSON object"]
    support = data.get("evidence_support")
    if support not in EVIDENCE_SUPPORT:
        errs.append(f"evidence_support: {support!r} not in {sorted(EVIDENCE_SUPPORT)}")
    valid = data.get("annotation_valid")
    if not isinstance(valid, bool):
        errs.append(f"annotation_valid: {valid!r} must be a bool")
    elif valid and support not in ("strong", "moderate"):
        errs.append(
            f"annotation_valid=true requires evidence_support in ('strong','moderate') (got {support!r})"
        )
    overtrigger = data.get("possible_overtrigger")
    if not isinstance(overtrigger, bool):
        errs.append(f"possible_overtrigger: {overtrigger!r} must be a bool")
    conflicting = data.get("conflicting_codes") or []
    if not isinstance(conflicting, list):
        errs.append("conflicting_codes: must be a list")
        conflicting = []
    for i, code in enumerate(conflicting):
        if not isinstance(code, str):
            errs.append(f"conflicting_codes[{i}]: must be a string")
        elif catalog_codes and code not in catalog_codes:
            errs.append(f"conflicting_codes[{i}]: {code!r} not in taxonomy catalog")
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        errs.append("rationale: must be a non-empty string")
    return errs


def run_selection(taxonomy: Taxonomy, trace_text: str, *, judge_model: str,
                  llm_call: JudgeCallable | None = None) -> SelectionJudgeResult:
    return SelectionJudge(taxonomy, judge_model=judge_model, llm_call=llm_call).run(trace_text)


def run_mapping(taxonomy: Taxonomy, failure_point: Mapping[str, Any], *, judge_model: str,
                llm_call: JudgeCallable | None = None) -> MappingJudgeResult:
    return MappingJudge(taxonomy, judge_model=judge_model, llm_call=llm_call).run(failure_point)


def run_coverage(taxonomy: Taxonomy, payload: Mapping[str, Any], *, judge_model: str,
                 llm_call: JudgeCallable | None = None) -> CoverageJudgeResult:
    return CoverageJudge(taxonomy, judge_model=judge_model, llm_call=llm_call).run(payload)


def run_quality(taxonomy: Taxonomy, support_traces: Sequence[str] | None = None, *,
                judge_model: str, llm_call: JudgeCallable | None = None) -> QualityJudgeResult:
    return QualityJudge(taxonomy, judge_model=judge_model, llm_call=llm_call).run(support_traces)


def run_calibration(taxonomy: Taxonomy, payload: Mapping[str, Any], *, judge_model: str,
                    llm_call: JudgeCallable | None = None) -> CalibrationJudgeResult:
    return CalibrationJudge(taxonomy, judge_model=judge_model, llm_call=llm_call).run(payload)


__all__ = [
    "SIMPLE_JUDGE_TYPES",
    "JudgeController",
    "load_judge_definition",
    "render_judge_prompt",
    "SelectionJudge",
    "SelectionJudgeResult",
    "MappingJudge",
    "MappingJudgeResult",
    "CoverageJudge",
    "CoverageJudgeResult",
    "QualityJudge",
    "QualityJudgeResult",
    "CalibrationJudge",
    "CalibrationJudgeResult",
    "validate_selection_output",
    "validate_mapping_output",
    "validate_coverage_output",
    "validate_quality_output",
    "validate_calibration_output",
    "run_selection",
    "run_mapping",
    "run_coverage",
    "run_quality",
    "run_calibration",
]
