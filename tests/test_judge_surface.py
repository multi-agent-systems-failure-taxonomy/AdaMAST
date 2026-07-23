"""Evaluation-only regression checks for the AdaMAST-as-a-judge surface.

These tests exercise scoring and evidence behavior for classifying completed
agent traces against a taxonomy — id-shape-agnostic citation matching, judge
output filtering, and fired-code evidence accounting. They are deliberately
offline: every LLM boundary is injected. Run from the repository root with:

    python -m pytest AdaMAST_as_a_Judge/tests -q
"""

from __future__ import annotations

import json
from pathlib import Path

from adamast.core.evidence import record_reflection
from adamast.core.reflection import CodeAssignment, ReflectionResult, mentioned_codes
from adamast.core.taxonomy_data import Taxonomy

from adamast.judges import SelectionJudge, selection_summary_judge
from adamast.judges.reflection_judge import AdaMASTReflectionJudge, validate_output


SPARSE_FLAT = {
    "taxonomy_id": "tax-judge-surface",
    "repo": "paper_eval",
    "domain": "doc-qa",
    "codes": [
        {"id": "A.4", "name": "Task_Refusal", "category": "A",
         "description": "The agent gives up instead of answering."},
        {"id": "A.10", "name": "Instruction_Noncompliance", "category": "A",
         "description": "The agent violates explicit format instructions."},
        {"id": "MAST-12", "name": "Verifier_Skipped", "category": "B",
         "description": "No verification pass before the final answer."},
        {"id": "7", "name": "Bare_Numeric_Mode", "category": "C",
         "description": "A bare-numeric-id failure mode."},
    ],
}


def sparse_taxonomy() -> Taxonomy:
    """Taxonomy whose catalog carries the registered (sparse, mixed-shape) ids."""
    return Taxonomy.from_flat(SPARSE_FLAT)


KNOWN_IDS = [c["id"] for c in SPARSE_FLAT["codes"]]


class TestCitationGrammar:
    """The single shared citation matcher must be id-shape-agnostic."""

    def test_every_registered_shape_matches(self):
        text = "Fired A.10 and MAST-12 and 7 in this segment."
        assert mentioned_codes(text, KNOWN_IDS) == ("A.10", "MAST-12", "7")

    def test_dotted_prefix_never_matches_inside_longer_id(self):
        # A.1 is not in the taxonomy; it must not match inside A.10.
        assert mentioned_codes("clearly A.10 fired", ["A.1"]) == ()

    def test_numeric_id_is_boundary_guarded(self):
        assert mentioned_codes("17 items processed", ["7"]) == ()
        assert mentioned_codes("code 7 fired", ["7"]) == ("7",)

    def test_case_insensitive(self):
        assert mentioned_codes("mast-12 applies", ["MAST-12"]) == ("MAST-12",)


class TestSelectionJudgeScoring:
    """Selection output is filtered to catalog codes with evidence."""

    def run_with(self, payload: dict):
        tax = sparse_taxonomy()
        judge = SelectionJudge(
            tax,
            judge_model="injected",
            llm_call=lambda prompt, model: json.dumps(payload),
        )
        return judge.run("trace text")

    def test_valid_codes_kept_in_catalog_shape(self):
        result = self.run_with({
            "failure_modes": [
                {"code": "A.10", "evidence": "format violated", "confidence": "high",
                 "severity": "moderate"},
                {"code": "MAST-12", "evidence": "no check step", "confidence": "medium",
                 "severity": "minor"},
            ],
            "none_apply": False,
        })
        assert result.code_ids() == ["A.10", "MAST-12"]
        assert all(m["evidence"].strip() for m in result.failure_modes)

    def test_alien_code_dropped_with_warning(self):
        result = self.run_with({
            "failure_modes": [
                {"code": "Z.99", "evidence": "invented", "confidence": "high",
                 "severity": "major"},
                {"code": "A.4", "evidence": "real", "confidence": "high",
                 "severity": "major"},
            ],
            "none_apply": False,
        })
        assert result.code_ids() == ["A.4"]
        assert any("Z.99" in warning for warning in result.judge_metadata["warnings"])

    def test_none_apply_requires_empty_modes(self):
        result = self.run_with({
            "failure_modes": [
                {"code": "A.4", "evidence": "e", "confidence": "high",
                 "severity": "major"},
            ],
            "none_apply": True,
        })
        # conflicting payload: fired modes win, none_apply demoted, warned
        assert result.code_ids() == ["A.4"]
        assert result.none_apply is False
        assert result.judge_metadata["warnings"]


def _analysis_payload() -> dict:
    return {
        "trace_summary": {"overall_judgment": "failure"},
        "events": [{"event_id": "e1", "summary": "wrong answer emitted"}],
        "failure_points": [{
            "failure_point_id": "fp1",
            "summary": "final answer ignores the format instruction",
            "observed_evidence": "answer emitted as prose, task demanded numeric",
            "reason_observed_or_inferred": "observed",
            "evidence_strength": "direct",
            "judge_confidence": 0.9,
            "causal_role": "root_cause",
            "recovery_status": "unrecovered",
            "present_in_final_output": "yes",
            "objective_relevance": "final_output_relevant",
            "severity": "major",
            "outcome_link": "direct",
            "candidate_attribution": "high",
            "external_attribution": "none",
            "actionability": "high",
        }],
        "relations": [],
    }


class TestReflectionJudgeScoring:
    def run_with_mapping(self, mappings: list[dict]) -> dict:
        def fake_llm(user, system, *, max_tokens=0, meter=None, warnings=None):
            if "taxonomy mapping stage" in system:
                return {"mappings_by_failure_point": [{
                    "failure_point_id": "fp1",
                    "taxonomy_mappings": mappings,
                    "unmapped": False,
                }]}
            return _analysis_payload()

        judge = AdaMASTReflectionJudge(
            sparse_taxonomy(), judge_model="injected", llm_call=fake_llm,
        )
        return judge.analyze({"candidate_id": "c", "task_id": "t", "run_id": "r",
                              "trace": "trace"})

    def test_catalog_codes_flow_to_selection_summary(self):
        out = self.run_with_mapping([
            {"code": "A.10", "primary_or_secondary": "primary",
             "mapping_confidence": 0.9, "rationale": "format ignored"},
        ])
        assert validate_output(out) == []
        assert out["selection_summary"]["root_failure_modes"] == ["A.10"]

    def test_alien_mapping_codes_are_dropped_and_warned(self):
        out = self.run_with_mapping([
            {"code": "Z.99", "primary_or_secondary": "primary",
             "mapping_confidence": 0.9, "rationale": "invented"},
            {"code": "MAST-12", "primary_or_secondary": "secondary",
             "mapping_confidence": 0.7, "rationale": "real"},
        ])
        fired = [m["code"] for fp in out["failure_points"]
                 for m in fp["taxonomy_mappings"]]
        assert fired == ["MAST-12"]
        assert "Z.99" not in json.dumps(out["selection_summary"])
        assert any("Z.99" in w for w in out["judge_metadata"]["warnings"])

    def test_selection_summary_judge_matches_reflection_derivation(self):
        out = self.run_with_mapping([
            {"code": "A.4", "primary_or_secondary": "primary",
             "mapping_confidence": 0.4, "rationale": "weak fit"},
        ])
        rerun = selection_summary_judge.run(
            out["failure_points"], out["relations"], weak_threshold=0.65,
        )
        assert rerun == out["selection_summary"]
        weak_codes = [entry["code"] for entry in rerun["weak_taxonomy_matches"]]
        assert weak_codes == ["A.4"]


class TestEvidenceScoring:
    def test_fired_dotted_code_increments_fire_count(self, tmp_path: Path):
        state = {"taxonomy_id": "tax-judge-surface", "session_id": "sess-1"}
        reflection = ReflectionResult(
            checkpoint_id="cp-1",
            observe="format violated",
            assignments=(CodeAssignment("A.10", "prose answer vs numeric demand"),),
            considered_codes=("A.10",),
            none_apply=False,
            correlate="instruction was explicit",
            decide="fix the format",
        )
        record_reflection(tmp_path, state, reflection, gate="stop",
                          task_id="sess-1", agent_type="judge_eval")
        record_reflection(tmp_path, state, reflection, gate="stop2",
                          task_id="sess-1", agent_type="judge_eval")
        data = json.loads((tmp_path / ".adamast-runtime-evidence.json").read_text())
        code_entry = data["taxonomies"]["tax-judge-surface"]["codes"]["A.10"]
        assert code_entry["fire_count"] == 2
        assert all(e["evidence"] for e in code_entry["events"])
        assert [c["fired_codes"] for c in data["checkpoints"]] == [["A.10"], ["A.10"]]


class TestIdStability:
    """Registered ids are identities and survive the refinement round-trip."""

    def test_from_flat_preserves_registered_ids(self):
        tax = Taxonomy.from_flat(SPARSE_FLAT)
        assert [c.code for c in tax.codes] == KNOWN_IDS
