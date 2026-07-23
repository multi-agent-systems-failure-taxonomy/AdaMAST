"""Tests for the adamast.judges/ folder structure.

Validates the registry, placeholder behavior, and the real
selection_summary_judge wrapper around derive_selection_summary.
"""

import unittest

import adamast.judges
from adamast.judges import (
    SIMPLE_JUDGE_TYPES,
    load_judge_definition,
    selection_summary_judge,
)
from adamast.judges.reflection_judge import (
    AdaMASTReflectionJudge,
    derive_selection_summary,
    validate_output,
)


class RegistryTests(unittest.TestCase):
    def test_real_and_placeholder_sets_are_disjoint_and_cover_all(self) -> None:
        self.assertEqual(set(adamast.judges.REAL) & set(adamast.judges.PLACEHOLDER), set())
        self.assertEqual(set(adamast.judges.ALL),
                         set(adamast.judges.REAL) | set(adamast.judges.PLACEHOLDER))
        # 7 total per the canonical taxonomy.
        self.assertEqual(len(adamast.judges.ALL), 7)

    def test_all_seven_judges_are_now_real(self) -> None:
        # After the implementation pass, every judge has a working
        # implementation; PLACEHOLDER is empty.
        for name in (
            "selection", "reflection_judge", "mapping",
            "coverage", "quality", "calibration",
            "selection_summary_judge",
        ):
            self.assertIn(name, adamast.judges.REAL, f"{name} should be REAL")
        self.assertEqual(adamast.judges.PLACEHOLDER, ())

    def test_simple_judge_assets_are_loadable(self) -> None:
        self.assertEqual(
            set(SIMPLE_JUDGE_TYPES),
            {"selection", "mapping", "coverage", "quality", "calibration"},
        )
        for judge_type in SIMPLE_JUDGE_TYPES:
            definition = load_judge_definition(judge_type)
            self.assertIn("You are", definition["system"])
            self.assertIn("OUTPUT (JSON only)", definition["user_template"])


class SelectionSummaryJudgeTests(unittest.TestCase):
    def test_empty_input_returns_all_buckets(self) -> None:
        out = selection_summary_judge.run([], [])
        # All 12 canonical buckets must be present even for empty input.
        expected = {
            "root_failure_modes", "candidate_attributable_failure_modes",
            "external_or_environmental_failure_modes",
            "unrecovered_failure_modes", "recovered_failure_modes",
            "terminal_symptom_modes", "isolated_failure_modes",
            "actionable_failure_modes", "high_severity_failure_modes",
            "outcome_linked_failure_modes",
            "unmapped_failure_points", "weak_taxonomy_matches",
        }
        self.assertEqual(set(out.keys()), expected)
        for k in expected:
            self.assertEqual(out[k], [])

    def test_root_cause_with_mapping_lands_in_root_bucket(self) -> None:
        fps = [{
            "failure_point_id": "F1",
            "causal_role": "root_cause",
            "recovery_status": "unrecovered",
            "actionability": "high",
            "severity": "critical",
            "outcome_link": "direct",
            "candidate_attribution": "high",
            "external_attribution": "none",
            "taxonomy_mappings": [
                {"code": "A.1", "primary_or_secondary": "primary",
                 "mapping_confidence": 0.9}
            ],
        }]
        out = selection_summary_judge.run(fps)
        self.assertIn("A.1", out["root_failure_modes"])
        self.assertIn("A.1", out["unrecovered_failure_modes"])
        self.assertIn("A.1", out["actionable_failure_modes"])
        self.assertIn("A.1", out["high_severity_failure_modes"])

    def test_weak_mapping_below_threshold_surfaces(self) -> None:
        fps = [{
            "failure_point_id": "F1",
            "causal_role": "unclear",
            "recovery_status": "not_applicable",
            "actionability": "low",
            "severity": "minor",
            "outcome_link": "unlikely",
            "candidate_attribution": "low",
            "external_attribution": "none",
            "taxonomy_mappings": [
                {"code": "A.2", "primary_or_secondary": "primary",
                 "mapping_confidence": 0.3,
                 "mapping_rationale": "weak fit"}
            ],
        }]
        out = selection_summary_judge.run(fps, weak_threshold=0.5)
        weak = out["weak_taxonomy_matches"]
        self.assertEqual(len(weak), 1)
        self.assertEqual(weak[0]["code"], "A.2")


class ReflectionJudgeShellTests(unittest.TestCase):
    """Construct the judge with a stub LLM to confirm wiring works.

    We don't exercise the prompt — that would mean an LLM call. The point is
    to prove import/construction is sane and that the public surface keeps
    model selection explicit.
    """

    def test_requires_judge_model(self) -> None:
        from adamast.core.taxonomy_data import Taxonomy
        tax = Taxonomy.from_flat({"repo": "x", "domain": "y", "codes": []})
        with self.assertRaises(ValueError):
            AdaMASTReflectionJudge(tax, judge_model="")

    def test_rejects_unknown_mode(self) -> None:
        from adamast.core.taxonomy_data import Taxonomy
        tax = Taxonomy.from_flat({"repo": "x", "domain": "y", "codes": []})
        with self.assertRaises(ValueError):
            AdaMASTReflectionJudge(tax, judge_model="m", mode="bogus")

    def test_analyze_with_stub_llm_returns_envelope(self) -> None:
        from adamast.core.taxonomy_data import Taxonomy
        tax = Taxonomy.from_flat({"repo": "x", "domain": "y", "codes": []})

        def stub_llm(user, system, *, max_tokens=8192, meter=None, warnings=None):
            # Trivially valid analysis result with zero failure points.
            return {
                "trace_summary": {"overall_judgment": "success"},
                "events": [],
                "failure_points": [],
                "relations": [],
            }

        judge = AdaMASTReflectionJudge(tax, judge_model="stub", llm_call=stub_llm)
        out = judge.analyze({
            "candidate_id": "c", "task_id": "t", "run_id": "r",
            "task_prompt": "do thing", "candidate_output": "result",
            "trace": "...",
        })
        self.assertEqual(out["candidate_id"], "c")
        self.assertEqual(out["judge_metadata"]["judge_model"], "stub")
        self.assertEqual(out["failure_points"], [])
        # selection_summary must be the deterministic derivation, not from LLM.
        self.assertIn("root_failure_modes", out["selection_summary"])


class SchemaValidatorTests(unittest.TestCase):
    def test_validate_output_flags_missing_top_level(self) -> None:
        errs = validate_output({})
        self.assertTrue(errs)

    def test_validate_output_accepts_minimal_valid_envelope(self) -> None:
        envelope = {
            "candidate_id": "c", "task_id": "t", "run_id": "r",
            "judge_metadata": {},
            "trace_summary": {"overall_judgment": "success"},
            "events": [],
            "failure_points": [],
            "relations": [],
            "selection_summary": {},
            "reflection_summary": {},
        }
        self.assertEqual(validate_output(envelope), [])


class DeriveSelectionSummaryTests(unittest.TestCase):
    """The selection.py module is a pure function — exercise it directly too."""

    def test_unmapped_failure_point_recorded(self) -> None:
        fps = [{
            "failure_point_id": "F1",
            "causal_role": "root_cause",
            "recovery_status": "unrecovered",
            "actionability": "high",
            "severity": "critical",
            "outcome_link": "direct",
            "candidate_attribution": "high",
            "external_attribution": "none",
            "taxonomy_mappings": [],
            "unmapped": True,
            "proposed_failure_mode": {"name": "NewMode", "definition": "..."},
            "ruled_out_codes": [{"code": "A.1", "reason": "doesn't fit"}],
        }]
        out = derive_selection_summary(fps)
        self.assertEqual(len(out["unmapped_failure_points"]), 1)
        self.assertEqual(out["unmapped_failure_points"][0]["proposed_name"], "NewMode")


class PartialValidateTests(unittest.TestCase):
    """The analysis-stage validator must defer mapping-stage requirements
    without swallowing errors about genuinely malformed mapping fields."""

    @staticmethod
    def _judge():
        from adamast.core.taxonomy_data import Taxonomy

        tax = Taxonomy.from_flat({"repo": "x", "domain": "y", "codes": []})
        return AdaMASTReflectionJudge(tax, judge_model="stub", llm_call=lambda *a, **k: {})

    @staticmethod
    def _analysis_failure_point(**overrides):
        fp = {
            "failure_point_id": "fp-1",
            "summary": "a concrete failure",
            "observed_evidence": "the command failed with exit 1",
            "reason_observed_or_inferred": "observed",
            "evidence_strength": "high",
            "judge_confidence": 0.8,
            "causal_role": "root_cause",
            "recovery_status": "unrecovered",
            "present_in_final_output": "yes",
            "objective_relevance": "main_objective_relevant",
            "severity": "major",
            "outcome_link": "likely",
            "candidate_attribution": "high",
            "external_attribution": "none",
            "actionability": "high",
        }
        fp.update(overrides)
        return fp

    def test_absent_mapping_fields_are_deferred(self):
        judge = self._judge()
        partial = {
            "trace_summary": {"overall_judgment": "failure"},
            "events": [],
            "failure_points": [self._analysis_failure_point()],
            "relations": [],
        }
        self.assertEqual(judge._partial_validate(partial), [])

    def test_malformed_taxonomy_mappings_are_no_longer_swallowed(self):
        # Regression: the old substring filter dropped ANY error mentioning
        # taxonomy_mappings, including this genuinely broken one.
        judge = self._judge()
        partial = {
            "trace_summary": {"overall_judgment": "failure"},
            "events": [],
            "failure_points": [
                self._analysis_failure_point(
                    taxonomy_mappings=[{"bogus": True}],
                )
            ],
            "relations": [],
        }
        errs = judge._partial_validate(partial)
        self.assertTrue(any("taxonomy_mappings" in e for e in errs))

    def test_unmapped_with_partial_rationale_is_validated(self):
        judge = self._judge()
        partial = {
            "trace_summary": {"overall_judgment": "failure"},
            "events": [],
            "failure_points": [
                self._analysis_failure_point(
                    unmapped=True,
                    ruled_out_codes=[{"code": "A.1"}],  # missing reason
                )
            ],
            "relations": [],
        }
        errs = judge._partial_validate(partial)
        self.assertTrue(any("ruled_out_codes" in e for e in errs))

    def test_unmapped_without_any_rationale_is_deferred(self):
        judge = self._judge()
        partial = {
            "trace_summary": {"overall_judgment": "failure"},
            "events": [],
            "failure_points": [self._analysis_failure_point(unmapped=True)],
            "relations": [],
        }
        self.assertEqual(judge._partial_validate(partial), [])


if __name__ == "__main__":
    unittest.main()
