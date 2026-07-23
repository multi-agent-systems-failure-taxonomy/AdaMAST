"""Per-judge tests for the 5 real LLM-based judges.

Every test uses a stubbed LLM caller (``stub_llm(response)``) so no live
API calls are made. The tests cover:
  * happy-path: valid LLM output -> correct typed result
  * validation salvage: malformed/bad output -> warnings + salvaged result
  * consistency rules: enforced even when the LLM violates them
  * catalog-code filtering: codes outside the taxonomy are dropped
"""

from __future__ import annotations

import json
import unittest

from adamast.core.taxonomy_data import Taxonomy
from adamast.judges import (
    CalibrationJudge,
    CoverageJudge,
    JudgeController,
    MappingJudge,
    QualityJudge,
    SelectionJudge,
)


def make_tax() -> Taxonomy:
    return Taxonomy.from_flat({
        "repo": "r", "domain": "d",
        "codes": [
            {"id": "A.1", "name": "Loop",
             "description": "agent loops on the same tool call",
             "category": "A", "severity": "major"},
            {"id": "A.2", "name": "PrematureTerminal",
             "description": "agent stops before verifying the result",
             "category": "A", "severity": "moderate"},
            {"id": "C.1", "name": "BadMath",
             "description": "off-by-one in numerical reasoning",
             "category": "C", "severity": "minor"},
        ],
    })


def stub_llm(response):
    """Build an llm_call that returns ``response`` (dict or str) once."""
    if isinstance(response, dict):
        text = json.dumps(response)
    else:
        text = str(response)
    return lambda prompt, model: text


# ──────────────────────────────────────────────────────────────────────────
# Selection Judge
# ──────────────────────────────────────────────────────────────────────────


class SelectionJudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tax = make_tax()

    def test_happy_path_returns_typed_result(self):
        llm = stub_llm({
            "failure_modes": [
                {"code": "A.1", "name": "Loop",
                 "evidence": "three identical Bash retries",
                 "confidence": "high", "severity": "major"}
            ],
            "none_apply": False,
        })
        judge = SelectionJudge(self.tax, judge_model="m", llm_call=llm)
        result = judge.run("trace")
        self.assertEqual(result.code_ids(), ["A.1"])
        self.assertFalse(result.none_apply)
        self.assertEqual(result.judge_metadata["warnings"], [])

    def test_none_apply_path(self):
        llm = stub_llm({"failure_modes": [], "none_apply": True})
        judge = SelectionJudge(self.tax, judge_model="m", llm_call=llm)
        result = judge.run("trace")
        self.assertEqual(result.code_ids(), [])
        self.assertTrue(result.none_apply)

    def test_unknown_code_dropped_with_warning(self):
        llm = stub_llm({
            "failure_modes": [
                {"code": "Z.99", "name": "Fake", "evidence": "x",
                 "confidence": "high", "severity": "major"}
            ],
            "none_apply": False,
        })
        judge = SelectionJudge(self.tax, judge_model="m", llm_call=llm)
        result = judge.run("trace")
        self.assertEqual(result.code_ids(), [])
        joined = " | ".join(result.judge_metadata["warnings"])
        self.assertIn("not in taxonomy catalog", joined)

    def test_run_many_returns_one_result_per_input(self):
        llm = stub_llm({"failure_modes": [], "none_apply": True})
        judge = SelectionJudge(self.tax, judge_model="m", llm_call=llm)
        results = judge.run_many(["t1", "t2", "t3"])
        self.assertEqual(len(results), 3)

    def test_requires_judge_model(self):
        with self.assertRaises(ValueError):
            SelectionJudge(self.tax, judge_model="")

    def test_general_controller_can_run_selection_asset(self):
        llm = stub_llm({"failure_modes": [], "none_apply": True})
        result = JudgeController(
            "selection", self.tax, judge_model="m", llm_call=llm
        ).run("trace")
        self.assertTrue(result.none_apply)


# ──────────────────────────────────────────────────────────────────────────
# Mapping Judge
# ──────────────────────────────────────────────────────────────────────────


class MappingJudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tax = make_tax()
        self.fp = {
            "failure_point_id": "F1",
            "summary": "Agent looped",
            "observed_evidence": "three identical Bash retries",
        }

    def test_mapped_primary_plus_secondary(self):
        llm = stub_llm({
            "primary_code": "A.1", "secondary_codes": ["C.1"],
            "mapping_confidence": 0.85, "unmapped": False,
            "ruled_out_codes": [], "proposed_failure_mode": None,
        })
        result = MappingJudge(self.tax, judge_model="m",
                              llm_call=llm).run(self.fp)
        self.assertEqual(result.primary_code, "A.1")
        self.assertEqual(result.secondary_codes, ["C.1"])
        self.assertAlmostEqual(result.mapping_confidence, 0.85)
        self.assertFalse(result.unmapped)

    def test_unmapped_requires_proposed_failure_mode(self):
        llm = stub_llm({
            "primary_code": None, "secondary_codes": [],
            "mapping_confidence": 0.0, "unmapped": True,
            "ruled_out_codes": [{"code": "A.1", "reason": "wrong shape"}],
            "proposed_failure_mode": {"name": "NewMode", "definition": "..."},
        })
        result = MappingJudge(self.tax, judge_model="m",
                              llm_call=llm).run(self.fp)
        self.assertTrue(result.unmapped)
        self.assertIsNone(result.primary_code)
        self.assertEqual(result.proposed_failure_mode["name"], "NewMode")
        self.assertEqual(len(result.ruled_out_codes), 1)

    def test_invalid_primary_code_is_dropped(self):
        llm = stub_llm({
            "primary_code": "Z.99", "secondary_codes": ["A.1"],
            "mapping_confidence": 0.5, "unmapped": False,
            "ruled_out_codes": [], "proposed_failure_mode": None,
        })
        result = MappingJudge(self.tax, judge_model="m",
                              llm_call=llm).run(self.fp)
        self.assertIsNone(result.primary_code)
        self.assertIn("A.1", result.secondary_codes)


# ──────────────────────────────────────────────────────────────────────────
# Coverage Judge
# ──────────────────────────────────────────────────────────────────────────


class CoverageJudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tax = make_tax()

    def test_partially_covered_with_proposal(self):
        llm = stub_llm({
            "coverage_status": "partially_covered",
            "closest_codes": ["A.1"],
            "missing_failure_pattern": "tool-quota recovery not captured",
            "suggest_new_code": True,
            "proposed_failure_mode": {"name": "QuotaRecoveryGap",
                                      "definition": "no fallback after quota"},
        })
        r = CoverageJudge(self.tax, judge_model="m",
                          llm_call=llm).run({"trace": "Agent hit quota."})
        self.assertEqual(r.coverage_status, "partially_covered")
        self.assertEqual(r.closest_codes, ["A.1"])
        self.assertTrue(r.suggest_new_code)
        self.assertEqual(r.proposed_failure_mode["name"], "QuotaRecoveryGap")

    def test_covered_path_clears_proposal(self):
        llm = stub_llm({
            "coverage_status": "covered",
            "closest_codes": ["A.1"],
            "missing_failure_pattern": None,
            "suggest_new_code": False,
            "proposed_failure_mode": None,
        })
        r = CoverageJudge(self.tax, judge_model="m",
                          llm_call=llm).run({"trace": "looped"})
        self.assertEqual(r.coverage_status, "covered")
        self.assertIsNone(r.proposed_failure_mode)
        self.assertFalse(r.suggest_new_code)

    def test_requires_trace_or_failure_point(self):
        llm = stub_llm({"coverage_status": "covered", "closest_codes": [],
                        "missing_failure_pattern": None,
                        "suggest_new_code": False, "proposed_failure_mode": None})
        with self.assertRaises(ValueError):
            CoverageJudge(self.tax, judge_model="m",
                          llm_call=llm).run({})

    def test_suggest_without_proposal_is_demoted(self):
        # LLM violation: claims a new code is needed but doesn't propose one.
        llm = stub_llm({
            "coverage_status": "not_covered",
            "closest_codes": [],
            "missing_failure_pattern": "...",
            "suggest_new_code": True,
            "proposed_failure_mode": None,
        })
        r = CoverageJudge(self.tax, judge_model="m",
                          llm_call=llm).run({"trace": "x"})
        # Salvage logic forces suggest_new_code=False when no proposal.
        self.assertFalse(r.suggest_new_code)
        self.assertIsNone(r.proposed_failure_mode)


# ──────────────────────────────────────────────────────────────────────────
# Quality Judge
# ──────────────────────────────────────────────────────────────────────────


class QualityJudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tax = make_tax()

    def test_per_code_issues_returned(self):
        llm = stub_llm({
            "code_quality": [
                {"code": "A.1",
                 "issue": "definition is too vague",
                 "recommendation": "add concrete detection heuristics"}
            ],
            "overall_quality": "needs_refinement",
            "overall_summary": "One vague code; rest are fine.",
        })
        r = QualityJudge(self.tax, judge_model="m",
                         llm_call=llm).run()
        self.assertEqual(len(r.code_quality), 1)
        self.assertEqual(r.code_quality[0]["code"], "A.1")
        self.assertEqual(r.overall_quality, "needs_refinement")

    def test_unknown_code_in_issues_dropped(self):
        llm = stub_llm({
            "code_quality": [
                {"code": "Z.99", "issue": "x", "recommendation": "y"},
                {"code": "A.1", "issue": "x", "recommendation": "y"},
            ],
            "overall_quality": "good",
            "overall_summary": "ok",
        })
        r = QualityJudge(self.tax, judge_model="m",
                         llm_call=llm).run()
        # Z.99 dropped; A.1 kept.
        kept_codes = [item["code"] for item in r.code_quality]
        self.assertEqual(kept_codes, ["A.1"])

    def test_support_traces_included_in_prompt(self):
        captured = {"prompt": None}

        def cap_llm(prompt, model):
            captured["prompt"] = prompt
            return json.dumps({"code_quality": [], "overall_quality": "good",
                               "overall_summary": "fine"})

        QualityJudge(self.tax, judge_model="m",
                     llm_call=cap_llm).run(
            support_traces=["the agent looped here", "and then halted"])
        self.assertIn("support trace 1", captured["prompt"])
        self.assertIn("the agent looped here", captured["prompt"])


# ──────────────────────────────────────────────────────────────────────────
# Calibration Judge
# ──────────────────────────────────────────────────────────────────────────


class CalibrationJudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tax = make_tax()

    def test_valid_strong_annotation(self):
        llm = stub_llm({
            "annotation_valid": True, "evidence_support": "strong",
            "possible_overtrigger": False, "conflicting_codes": [],
            "rationale": "evidence clearly satisfies the looping definition",
        })
        r = CalibrationJudge(self.tax, judge_model="m",
                             llm_call=llm).run({
            "annotation": {"code": "A.1", "confidence": "high"},
            "evidence": "three identical Bash calls",
        })
        self.assertTrue(r.annotation_valid)
        self.assertEqual(r.evidence_support, "strong")

    def test_weak_evidence_forces_invalid_even_when_llm_says_valid(self):
        llm = stub_llm({
            "annotation_valid": True, "evidence_support": "weak",
            "possible_overtrigger": False, "conflicting_codes": ["C.1"],
            "rationale": "stretched fit",
        })
        r = CalibrationJudge(self.tax, judge_model="m",
                             llm_call=llm).run({
            "annotation": {"code": "A.1", "confidence": "low"},
            "evidence": "one Bash call",
        })
        # Consistency rule: weak evidence cannot yield annotation_valid=True.
        self.assertFalse(r.annotation_valid)
        joined = " | ".join(r.judge_metadata["warnings"])
        self.assertIn("forcing annotation_valid=false", joined)

    def test_rejects_unknown_annotated_code(self):
        llm = stub_llm({})
        with self.assertRaises(ValueError):
            CalibrationJudge(self.tax, judge_model="m",
                             llm_call=llm).run({
                "annotation": {"code": "Z.99"},
                "evidence": "ok",
            })

    def test_rejects_missing_evidence(self):
        llm = stub_llm({})
        with self.assertRaises(ValueError):
            CalibrationJudge(self.tax, judge_model="m",
                             llm_call=llm).run({
                "annotation": {"code": "A.1"},
                "evidence": "",
            })

    def test_conflicting_codes_filtered_to_catalog_and_excludes_self(self):
        llm = stub_llm({
            "annotation_valid": True, "evidence_support": "moderate",
            "possible_overtrigger": True,
            "conflicting_codes": ["A.1", "C.1", "Z.99"],  # A.1 is self; Z.99 not in catalog
            "rationale": "could also fit C.1",
        })
        r = CalibrationJudge(self.tax, judge_model="m",
                             llm_call=llm).run({
            "annotation": {"code": "A.1", "confidence": "medium"},
            "evidence": "Bash retries with subtle variation",
        })
        self.assertEqual(r.conflicting_codes, ["C.1"])
        self.assertTrue(r.possible_overtrigger)


if __name__ == "__main__":
    unittest.main()
