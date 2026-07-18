"""Provider-neutral judges for applying an AdaMAST taxonomy to new traces.

The JUDGES layer is deliberately narrower than adaptive runtime integration:
it loads an existing taxonomy, asks one configured model to select the single
best-supported failure code for each trace, and returns validated structured
diagnoses. It does not accumulate traces, refine taxonomies, or modify an
agent harness.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .providers import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    TextProvider,
    create_provider,
    normalize_provider_name,
    resolve_model,
    validate_provider_credentials,
)
from .traces import load_traces


DEFAULT_MAX_TRACE_CHARS = 6000
JUDGE_SYSTEM_PROMPT = (
    "You are a failure diagnosis classifier for AI agents. "
    "Classify failures precisely based on evidence in the trace."
)


class JudgeResponseError(RuntimeError):
    """Raised when a model response cannot become a trustworthy diagnosis."""


@dataclass(frozen=True)
class Diagnosis:
    """Validated result of judging one trace against one taxonomy."""

    trace_id: str
    code: str
    label: str
    category: str
    evidence: str = ""
    confidence: float = 0.0
    recovery_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "code": self.code,
            "label": self.label,
            "category": self.category,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "recovery_hint": self.recovery_hint,
        }


class TaxonomyJudge:
    """Apply one existing taxonomy with one provider-neutral model adapter."""

    def __init__(
        self,
        taxonomy: Mapping[str, Any] | Path | str,
        provider: TextProvider,
        *,
        max_trace_chars: int = DEFAULT_MAX_TRACE_CHARS,
        allow_review_required: bool = False,
    ) -> None:
        if max_trace_chars <= 0:
            raise ValueError("max_trace_chars must be positive")

        self.taxonomy = _load_taxonomy(taxonomy)
        if (
            self.taxonomy.get("status") == "review_required"
            and not allow_review_required
        ):
            raise ValueError(
                "taxonomy status is review_required; use an accepted taxonomy "
                "or explicitly allow review-required input"
            )

        codes = _extract_codes(self.taxonomy)
        if not codes:
            raise ValueError("taxonomy contains no usable failure codes")

        self.provider = provider
        self.max_trace_chars = max_trace_chars
        self.codes = {code["id"]: code for code in codes}
        self._taxonomy_prompt = _format_codes(codes)

    def judge(self, trace: Mapping[str, Any]) -> Diagnosis:
        """Return the single best-supported taxonomy diagnosis for ``trace``."""

        trace_id = str(
            trace.get("problem_id") or trace.get("trace_id") or "unknown"
        )
        trace_text = _format_trace(trace, self.max_trace_chars)
        prompt = (
            f"Given this failure taxonomy:\n\n{self._taxonomy_prompt}\n\n"
            "Classify the following agent failure trace into ONE taxonomy code.\n\n"
            f"TRACE:\n{trace_text}\n\n"
            "Respond in this exact JSON format:\n"
            '{"code": "<code>", "label": "<label>", '
            '"evidence": "<specific evidence from trace>", '
            '"confidence": <0.0-1.0>, '
            '"recovery_hint": "<what to try differently>"}'
        )
        response = self.provider.complete(
            prompt,
            system=JUDGE_SYSTEM_PROMPT,
            response_format="json",
        )
        payload = _extract_json_object(response)
        return self._build_diagnosis(trace_id, payload)

    def judge_many(
        self, traces: Iterable[Mapping[str, Any]]
    ) -> list[Diagnosis]:
        """Judge multiple traces sequentially while preserving input order."""

        return [self.judge(trace) for trace in traces]

    def _build_diagnosis(
        self, trace_id: str, payload: Mapping[str, Any]
    ) -> Diagnosis:
        code_id = str(payload.get("code") or "").strip()
        if not code_id:
            raise JudgeResponseError(
                f"judge response for trace {trace_id!r} has no taxonomy code"
            )
        if code_id not in self.codes:
            raise JudgeResponseError(
                f"judge returned unknown taxonomy code {code_id!r} "
                f"for trace {trace_id!r}"
            )

        confidence_value = payload.get("confidence", 0.5)
        try:
            confidence = float(confidence_value)
        except (TypeError, ValueError) as exc:
            raise JudgeResponseError(
                f"judge returned invalid confidence {confidence_value!r} "
                f"for trace {trace_id!r}"
            ) from exc
        if not 0.0 <= confidence <= 1.0:
            raise JudgeResponseError(
                f"judge confidence must be between 0 and 1 for trace "
                f"{trace_id!r}"
            )

        code = self.codes[code_id]
        return Diagnosis(
            trace_id=trace_id,
            code=code_id,
            label=code["name"],
            category=code["category"],
            evidence=str(payload.get("evidence") or ""),
            confidence=confidence,
            recovery_hint=str(payload.get("recovery_hint") or ""),
        )


def create_judge(
    taxonomy: Mapping[str, Any] | Path | str,
    *,
    provider: str | None,
    model: str | None = None,
    max_trace_chars: int = DEFAULT_MAX_TRACE_CHARS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    aws_region: str | None = None,
    aws_profile: str | None = None,
    allow_review_required: bool = False,
) -> TaxonomyJudge:
    """Create a reusable judge using the same providers as BASELINE."""

    taxonomy_document = _load_taxonomy(taxonomy)
    provider_name = normalize_provider_name(provider)
    validate_provider_credentials(provider_name)
    model_name = resolve_model(provider_name, model)
    adapter = create_provider(
        provider_name,
        model_name,
        max_output_tokens=max_output_tokens,
        aws_region=aws_region,
        aws_profile=aws_profile,
    )
    return TaxonomyJudge(
        taxonomy_document,
        adapter,
        max_trace_chars=max_trace_chars,
        allow_review_required=allow_review_required,
    )


def judge_trace(
    taxonomy: Mapping[str, Any] | Path | str,
    trace: Mapping[str, Any],
    *,
    provider: str | None,
    model: str | None = None,
    max_trace_chars: int = DEFAULT_MAX_TRACE_CHARS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    aws_region: str | None = None,
    aws_profile: str | None = None,
    allow_review_required: bool = False,
) -> Diagnosis:
    """Judge one normalized trace against an existing taxonomy."""

    judge = create_judge(
        taxonomy,
        provider=provider,
        model=model,
        max_trace_chars=max_trace_chars,
        max_output_tokens=max_output_tokens,
        aws_region=aws_region,
        aws_profile=aws_profile,
        allow_review_required=allow_review_required,
    )
    return judge.judge(trace)


def judge_traces(
    taxonomy: Mapping[str, Any] | Path | str,
    traces: Iterable[Mapping[str, Any]] | Path | str,
    *,
    provider: str | None,
    model: str | None = None,
    max_trace_chars: int = DEFAULT_MAX_TRACE_CHARS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    aws_region: str | None = None,
    aws_profile: str | None = None,
    allow_review_required: bool = False,
) -> list[Diagnosis]:
    """Judge a trace iterable or every normalized trace in a file/directory."""

    if isinstance(traces, (str, Path)):
        trace_records = load_traces(traces)
    else:
        trace_records = list(traces)

    judge = create_judge(
        taxonomy,
        provider=provider,
        model=model,
        max_trace_chars=max_trace_chars,
        max_output_tokens=max_output_tokens,
        aws_region=aws_region,
        aws_profile=aws_profile,
        allow_review_required=allow_review_required,
    )
    return judge.judge_many(trace_records)


def _load_taxonomy(
    taxonomy: Mapping[str, Any] | Path | str,
) -> dict[str, Any]:
    if isinstance(taxonomy, Mapping):
        return dict(taxonomy)

    path = Path(taxonomy).expanduser().resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"could not read taxonomy {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"taxonomy is not valid JSON: {path}: {exc.msg}") from exc
    if not isinstance(document, dict):
        raise ValueError("taxonomy JSON must be an object")
    return document


def _extract_codes(taxonomy: Mapping[str, Any]) -> list[dict[str, str]]:
    """Read AdaMAST's public schema and legacy ATLAS layered taxonomies."""

    public_codes = taxonomy.get("codes")
    if isinstance(public_codes, list):
        codes = [
            _normalize_code(raw, fallback_id="", fallback_category="")
            for raw in public_codes
            if isinstance(raw, Mapping)
        ]
        return _deduplicate_codes(code for code in codes if code["id"])

    layers = (
        taxonomy.get("full_layer"),
        taxonomy.get("annotation_layer"),
        taxonomy,
    )
    for layer in layers:
        if not isinstance(layer, Mapping):
            continue
        codes: list[dict[str, str]] = []
        for key, category in (
            ("category_a", "A"),
            ("category_b", "B"),
            ("category_c", "C"),
        ):
            values = layer.get(key)
            if isinstance(values, Mapping):
                for fallback_id, raw in values.items():
                    if isinstance(raw, Mapping):
                        codes.append(
                            _normalize_code(
                                raw,
                                fallback_id=str(fallback_id),
                                fallback_category=category,
                            )
                        )
            elif isinstance(values, list):
                for raw in values:
                    if isinstance(raw, Mapping):
                        codes.append(
                            _normalize_code(
                                raw,
                                fallback_id="",
                                fallback_category=category,
                            )
                        )
        if codes:
            return _deduplicate_codes(
                code for code in codes if code["id"]
            )
    return []


def _normalize_code(
    raw: Mapping[str, Any],
    *,
    fallback_id: str,
    fallback_category: str,
) -> dict[str, str]:
    code_id = str(raw.get("id") or raw.get("code") or fallback_id).strip()
    category = str(raw.get("category") or fallback_category).strip()
    if not category and "." in code_id:
        category = code_id.split(".", 1)[0]
    return {
        "id": code_id,
        "name": str(raw.get("name") or raw.get("label") or code_id).strip(),
        "description": str(
            raw.get("description") or raw.get("definition") or ""
        ).strip(),
        "category": category,
        "when_to_use": str(raw.get("when_to_use") or "").strip(),
        "when_not_to_use": str(raw.get("when_not_to_use") or "").strip(),
    }


def _deduplicate_codes(
    codes: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for code in codes:
        if code["id"] in seen:
            continue
        seen.add(code["id"])
        unique.append(code)
    return unique


def _format_codes(codes: Iterable[Mapping[str, str]]) -> str:
    lines: list[str] = []
    for code in codes:
        lines.append(
            f"  {code['id']}: {code['name']} — {code['description']}"
        )
        if code.get("when_to_use"):
            lines.append(f"    Use when: {code['when_to_use']}")
        if code.get("when_not_to_use"):
            lines.append(f"    Do not use when: {code['when_not_to_use']}")
    return "\n".join(lines)


def _format_trace(trace: Mapping[str, Any], max_length: int) -> str:
    """Render a normalized trace with the ATLAS start/tail sampling policy."""

    trace_id = str(
        trace.get("problem_id") or trace.get("trace_id") or "unknown"
    )
    lines = [f"=== TRACE: {trace_id} ==="]

    task = str(trace.get("task") or "")
    if task:
        lines.extend((f"[TASK] {task[:400]}", ""))

    metadata = trace.get("metadata")
    if isinstance(metadata, Mapping):
        mas = str(metadata.get("mas_name") or "")
        model = str(metadata.get("llm_name") or "")
        if mas or model:
            lines.extend((f"[META] MAS: {mas}, LLM: {model}", ""))

    trajectory = trace.get("raw_trajectory")
    if trajectory is None:
        nested = trace.get("trace")
        if isinstance(nested, Mapping):
            trajectory = nested.get("trajectory")
        elif isinstance(nested, str):
            trajectory = nested
    if trajectory is None:
        raise ValueError(
            "judge traces must be normalized and include raw_trajectory; "
            "load file-based inputs with adamast.load_traces()"
        )

    trajectory_text = str(trajectory)
    if trajectory_text:
        header_length = len("\n".join(lines))
        available = max(0, max_length - header_length - 50)
        if len(trajectory_text) <= available:
            lines.append(trajectory_text)
        else:
            beginning = available * 2 // 5
            ending = available - beginning
            lines.extend(
                (
                    trajectory_text[:beginning],
                    "\n... [TRUNCATED] ...\n",
                    trajectory_text[-ending:] if ending else "",
                )
            )

    result = "\n".join(lines)
    if len(result) > max_length:
        return result[:max_length] + "\n... [truncated]"
    return result


def _extract_json_object(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise JudgeResponseError("judge returned an empty response")

    candidate = text.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        if start < 0:
            raise JudgeResponseError(
                "judge response did not contain a JSON object"
            )
        try:
            payload, _ = json.JSONDecoder().raw_decode(candidate[start:])
        except json.JSONDecodeError as exc:
            raise JudgeResponseError(
                f"judge returned invalid JSON: {exc.msg}"
            ) from exc

    if not isinstance(payload, dict):
        raise JudgeResponseError("judge response JSON must be an object")
    return payload
