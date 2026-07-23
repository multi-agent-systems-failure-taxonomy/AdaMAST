"""``AdaMASTReflectionJudge`` — orchestrates the multi-stage reflection judge.

``judge_model`` is required at construction time so model choice is explicit.
The default LLM transport routes through adamast's
``learning_calls.support_model_call`` (env-driven Anthropic + OpenAI + Gemini)
via the bridge in ``._llm``. Inject ``llm_call=...`` to override for tests.

Default ``mode="two_call"`` (recommended):
  1. Analysis call: trace → events, failure_points (no taxonomy), relations,
     causal-role/recovery/severity analysis. Validated; one retry on schema
     failure.
  2. Mapping call: failure_points (from #1) + taxonomy catalog → taxonomy
     codes per failure point. Validated; one retry on schema failure.
  3. Deterministic Python: derive the ``selection_summary``, build the final
     output object.

``mode="single_call"`` collapses #1 + #2 into one call.

The judge is testable: pass ``llm_call=fake_callable`` at construction. The
fake receives (user, system) strings + kwargs and returns a parsed dict.
"""

from __future__ import annotations

import copy
import json
import time
from typing import Any, Callable, Mapping, Optional

from adamast.core.taxonomy_data import CostMeter, Taxonomy

from adamast.llm.reflection_judge_llm import make_llm_call
from .prompts import (
    ANALYSIS_SYSTEM,
    JUDGE_PROMPT_VERSION,
    MAPPING_SYSTEM,
    SINGLE_CALL_SYSTEM,
    analysis_user_prompt,
    mapping_user_prompt,
    retry_user_prompt,
    single_call_user_prompt,
)
from .schema import validate_output
from .selection import derive_selection_summary

LLMCall = Callable[..., dict]


class AdaMASTReflectionJudge:
    """Multi-stage trace-analysis judge that emits a rich diagnostic graph
    + a compressed selection summary.

    Parameters
    ----------
    taxonomy : the failure-mode taxonomy (read-only, used to render the
               catalog for the mapping stage).
    judge_model : model id passed to the LLM transport. Required.
    meter : optional ``CostMeter`` to charge LLM spend against.
    mode : ``"two_call"`` (default, recommended) or ``"single_call"``.
    weak_mapping_threshold : taxonomy mappings with ``mapping_confidence`` <
        this threshold are surfaced in ``selection_summary.weak_taxonomy_matches``
        as a "definition drift" signal for the refinement gate.
    llm_call : LLM caller injected for testability. When None, builds a
        default bound to ``judge_model`` through adamast's transports.
        Must accept ``(prompt, system, *, max_tokens, meter, warnings)`` and
        return a parsed-JSON dict.
    judge_prompt_version : recorded in ``judge_metadata`` for reproducibility.
    """

    def __init__(
        self,
        taxonomy: Taxonomy,
        judge_model: str,
        *,
        meter: Optional[CostMeter] = None,
        mode: str = "two_call",
        weak_mapping_threshold: float = 0.65,
        llm_call: Optional[LLMCall] = None,
        judge_prompt_version: str = JUDGE_PROMPT_VERSION,
    ):
        if mode not in ("two_call", "single_call"):
            raise ValueError(f"unknown mode: {mode}")
        if not judge_model:
            raise ValueError("judge_model is required")
        self.taxonomy = taxonomy
        self.judge_model = judge_model
        self.meter = meter
        self.mode = mode
        self.weak_mapping_threshold = float(weak_mapping_threshold)
        self.llm_call: LLMCall = llm_call or make_llm_call(judge_model)
        self.judge_prompt_version = judge_prompt_version

    # ─────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────

    def analyze(self, judge_input: Mapping[str, Any]) -> dict:
        """Run the full multi-stage analysis on one trace.

        Returns the full structured output (see ``schema.py``).
        """
        warnings: list[str] = []
        if self.mode == "single_call":
            analysis = self._single_call(judge_input, warnings)
        else:
            analysis = self._two_call(judge_input, warnings)
        self._drop_alien_mappings(analysis, warnings)

        out = {
            "candidate_id": judge_input.get("candidate_id"),
            "task_id": judge_input.get("task_id"),
            "run_id": judge_input.get("run_id"),
            "judge_metadata": {
                "judge_model": self.judge_model,
                "judge_prompt_version": self.judge_prompt_version,
                "taxonomy_id": self.taxonomy.metadata.get("seed_path") or "",
                "taxonomy_version": self.taxonomy.version,
                "created_at": int(time.time()),
                "mode": self.mode,
                "warnings": warnings,
            },
            "trace_summary": analysis.get("trace_summary") or {},
            "events": analysis.get("events") or [],
            "failure_points": analysis.get("failure_points") or [],
            "relations": analysis.get("relations") or [],
            "selection_summary": derive_selection_summary(
                analysis.get("failure_points") or [],
                analysis.get("relations") or [],
                weak_threshold=self.weak_mapping_threshold,
            ),
            "reflection_summary": analysis.get("reflection_summary") or {
                "main_root_causes": [],
                "main_repair_targets": [],
                "mutation_hints": [],
                "what_not_to_overinterpret": [],
            },
        }
        return out

    # ─────────────────────────────────────────────────────────────────────
    # Two-call path (recommended)
    # ─────────────────────────────────────────────────────────────────────

    def _two_call(self, judge_input: Mapping[str, Any], warnings: list) -> dict:
        analysis_prompt = analysis_user_prompt(judge_input)
        analysis = self._call_with_retry(
            system=ANALYSIS_SYSTEM,
            user=analysis_prompt,
            stage="analysis",
            warnings=warnings,
            max_tokens=8192,
            require_full=False,
        )

        fps = analysis.get("failure_points") or []
        if not fps:
            return analysis

        slim = [
            {
                "failure_point_id": fp.get("failure_point_id"),
                "summary": fp.get("summary"),
                "observed_evidence": fp.get("observed_evidence"),
                "inferred_mechanism": fp.get("inferred_mechanism"),
                "stage": fp.get("stage"),
                "responsible_role": fp.get("responsible_role"),
                "responsible_agent": fp.get("responsible_agent"),
                "causal_role": fp.get("causal_role"),
                "severity": fp.get("severity"),
            }
            for fp in fps
        ]
        catalog = self.taxonomy.prompt_block()
        mapping_prompt = mapping_user_prompt(slim, catalog)
        mapping = self._call_simple(
            system=MAPPING_SYSTEM, user=mapping_prompt,
            stage="mapping", warnings=warnings, max_tokens=4096,
        )

        by_id = {m.get("failure_point_id"): m for m in (mapping.get("mappings_by_failure_point") or [])}
        for fp in fps:
            m = by_id.get(fp.get("failure_point_id")) or {}
            fp["taxonomy_mappings"] = m.get("taxonomy_mappings") or []
            fp["unmapped"] = bool(m.get("unmapped", False))
            if fp["unmapped"]:
                fp["ruled_out_codes"] = m.get("ruled_out_codes") or []
                fp["proposed_failure_mode"] = m.get("proposed_failure_mode")
            else:
                fp.pop("ruled_out_codes", None)
                fp.pop("proposed_failure_mode", None)
        analysis["failure_points"] = fps

        errs = validate_output(self._wrap_for_validation(analysis))
        if errs:
            warnings.append("post-merge validation issues: " + "; ".join(errs[:5]))
        return analysis

    # ─────────────────────────────────────────────────────────────────────
    # Single-call path
    # ─────────────────────────────────────────────────────────────────────

    def _single_call(self, judge_input: Mapping[str, Any], warnings: list) -> dict:
        catalog = self.taxonomy.prompt_block()
        user = single_call_user_prompt(judge_input, catalog)
        return self._call_with_retry(
            system=SINGLE_CALL_SYSTEM, user=user,
            stage="single_call", warnings=warnings,
            max_tokens=10240, require_full=True,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────

    def _call_simple(self, *, system: str, user: str, stage: str,
                     warnings: list, max_tokens: int) -> dict:
        """Call the LLM and parse JSON; no validation/retry."""
        stage_warnings: list = []
        try:
            out = self.llm_call(user, system, max_tokens=max_tokens,
                                meter=self.meter, warnings=stage_warnings)
        except TypeError:
            try:
                out = self.llm_call(user, system, max_tokens=max_tokens, meter=self.meter)
            except Exception as exc:
                warnings.append(f"{stage}: LLM call raised {type(exc).__name__}: {exc}")
                return {}
        except Exception as exc:
            warnings.append(f"{stage}: LLM call raised {type(exc).__name__}: {exc}")
            return {}
        for w in stage_warnings:
            warnings.append(f"{stage}: {w}")
        if not isinstance(out, dict):
            warnings.append(f"{stage}: LLM returned non-dict; using empty result")
            return {}
        return out

    def _call_with_retry(self, *, system: str, user: str, stage: str,
                         warnings: list, max_tokens: int,
                         require_full: bool) -> dict:
        """Call the LLM, parse, validate. Retry ONCE on empty or invalid."""
        first = self._call_simple(system=system, user=user, stage=stage,
                                  warnings=warnings, max_tokens=max_tokens)
        empty = not first
        errs: list[str] = []
        if not empty:
            errs = (validate_output(self._wrap_for_validation(first)) if require_full
                    else self._partial_validate(first))
        if not empty and not errs:
            return first

        if empty:
            warnings.append(f"{stage}: empty response; retrying with larger budget")
            retry_user = user
            retry_tokens = max(max_tokens, int(max_tokens * 1.5))
        else:
            warnings.append(f"{stage}: first response had {len(errs)} validation "
                            f"issues; retrying once")
            retry_user = retry_user_prompt(json.dumps(first, ensure_ascii=False), errs)
            retry_tokens = max_tokens

        second = self._call_simple(system=system, user=retry_user, stage=f"{stage}_retry",
                                   warnings=warnings, max_tokens=retry_tokens)
        if not second:
            warnings.append(f"{stage}: retry also failed; returning best-effort")
            return first
        errs2 = (validate_output(self._wrap_for_validation(second)) if require_full
                 else self._partial_validate(second))
        if errs2:
            warnings.append(f"{stage}: retry still has {len(errs2)} issues; "
                            f"using best-effort result")
        return second

    def _drop_alien_mappings(self, analysis: dict, warnings: list) -> None:
        """Drop taxonomy mappings whose code is not in the supplied taxonomy.

        The mapping stage instructs the model to use catalog codes only, but
        models still invent ids (or cite renumbered variants). The simple
        judges filter such codes; without this step the reflection judge
        propagated them into ``taxonomy_mappings`` and the derived
        ``selection_summary`` where downstream joins against the taxonomy
        silently miss.
        """
        known = {c.code for c in self.taxonomy.codes}
        for fp in analysis.get("failure_points") or []:
            if not isinstance(fp, dict):
                continue
            mappings = fp.get("taxonomy_mappings") or []
            if not isinstance(mappings, list):
                continue
            kept = [
                m for m in mappings
                if isinstance(m, Mapping) and m.get("code") in known
            ]
            if len(kept) != len(mappings):
                dropped = sorted({
                    str(m.get("code") if isinstance(m, Mapping) else m)
                    for m in mappings
                    if not (isinstance(m, Mapping) and m.get("code") in known)
                })
                warnings.append(
                    f"{fp.get('failure_point_id')}: dropped "
                    f"{len(mappings) - len(kept)} mapping(s) citing codes not "
                    f"in the taxonomy: {dropped}"
                )
                fp["taxonomy_mappings"] = kept

    def _partial_validate(self, partial: dict) -> list[str]:
        """Validator for the analysis-only output (Stage 1-7).

        Mapping-stage requirements (taxonomy_mappings; unmapped support
        fields) do not apply to analysis-only stages. Instead of filtering
        error messages by substring — which also swallowed errors about
        genuinely malformed mapping fields — inject neutral placeholders for
        the mapping-stage fields that are ABSENT, then run the full validator
        unfiltered. Any mapping field the stage did emit is validated for
        real.
        """
        doctored = copy.deepcopy(dict(partial))
        for fp in doctored.get("failure_points") or []:
            if not isinstance(fp, dict):
                continue
            if "taxonomy_mappings" not in fp:
                fp["taxonomy_mappings"] = []
            if (
                fp.get("unmapped")
                and "ruled_out_codes" not in fp
                and "proposed_failure_mode" not in fp
            ):
                # The unmapped rationale arrives with the mapping stage;
                # defer that rule only when the stage supplied neither part.
                fp["unmapped"] = False
        return validate_output(self._wrap_for_validation(doctored))

    @staticmethod
    def _wrap_for_validation(analysis: dict) -> dict:
        """Wrap an analysis-stage result in the full top-level envelope."""
        return {
            "candidate_id": "",
            "task_id": "",
            "run_id": "",
            "judge_metadata": {},
            "trace_summary": analysis.get("trace_summary") or {},
            "events": analysis.get("events") or [],
            "failure_points": analysis.get("failure_points") or [],
            "relations": analysis.get("relations") or [],
            "selection_summary": {},
            "reflection_summary": {},
        }
