"""Focused tests for tolerant-but-safe AdaMAST reflection parsing."""

from __future__ import annotations

import unittest

from adamast.core.reflection import harvest_reflection, parse_reflection


class ReflectionParserTests(unittest.TestCase):
    def test_accepts_markdown_heading_checkpoint_and_section_synonyms(self):
        result = parse_reflection(
            """# AdaMAST reflection

## Checkpoint ID: cp-1

## Observation
The run skipped verification.

## Mapping
- MAST-12 | Verification gap | evidence: "no tests were run"

## Root causes
The agent treated implementation as enough.

## Decision
change to run the relevant test before submission
""",
            checkpoint_id="cp-1",
            known_code_ids=("MAST-12",),
        )
        self.assertEqual(result.assignments[0].code_id, "MAST-12")
        self.assertIn("no tests", result.assignments[0].evidence)
        self.assertIn("change", result.decide)

    def test_still_requires_matching_checkpoint_id(self):
        with self.assertRaisesRegex(ValueError, "Checkpoint ID"):
            parse_reflection(
                """AdaMAST reflection:
- Observe: checked
- Map:
  - MAST-12 | evidence: "missing verification"
- Correlate: skipped
- Decide: change: verify
""",
                checkpoint_id="cp-1",
                known_code_ids=("MAST-12",),
            )

    def test_accepts_evidence_is_form(self):
        result = parse_reflection(
            """AdaMAST reflection:
- Checkpoint ID: cp-2
- Observe: checked
- Mapping:
  - MAST-12 | evidence is "the final answer lacks validation"
- Causal: rushed completion
- Action: change: validate first
""",
            checkpoint_id="cp-2",
            known_code_ids=("MAST-12",),
        )
        self.assertEqual(
            result.assignments[0].evidence,
            "the final answer lacks validation",
        )

    def test_accepts_clean_map_with_codes_checked_summary(self):
        result = parse_reflection(
            """AdaMAST reflection:
- Checkpoint ID: cp-3
- Observe: The task is complete and no failure evidence is present.
- Map:
  - none apply | evidence: "No failure evidence is present."
- Correlate: No recurring failure pattern is visible.
- Decide: submit: no change needed.

Final AdaMAST status: READY_TO_SUBMIT
Codes checked: MAST-1, MAST-12
Evidence: No failure evidence is present.
Final decision: ready
""",
            checkpoint_id="cp-3",
            known_code_ids=("MAST-1", "MAST-12"),
        )
        self.assertTrue(result.none_apply)
        self.assertEqual(result.assignments, ())
        self.assertEqual(result.considered_codes, ("MAST-1", "MAST-12"))


class HarvestReflectionTests(unittest.TestCase):
    def test_valid_content_with_wrong_checkpoint_id_is_corrected(self):
        harvest = harvest_reflection(
            """AdaMAST reflection:
- Checkpoint ID: stale-id
- Observe: checked
- Map:
  - MAST-12 | evidence: "missing verification"
- Correlate: skipped
- Decide: change: verify
""",
            checkpoint_id="cp-1",
            known_code_ids=("MAST-12",),
        )
        self.assertTrue(harvest.id_corrected)
        self.assertIsNotNone(harvest.result)
        self.assertEqual(harvest.result.checkpoint_id, "cp-1")
        self.assertEqual(harvest.found_checkpoint_id, "stale-id")

    def test_matching_id_is_not_marked_corrected(self):
        harvest = harvest_reflection(
            """AdaMAST reflection:
- Checkpoint ID: cp-1
- Observe: checked
- Map:
  - MAST-12 | evidence: "missing verification"
- Correlate: skipped
- Decide: change: verify
""",
            checkpoint_id="cp-1",
            known_code_ids=("MAST-12",),
        )
        self.assertFalse(harvest.id_corrected)
        self.assertIsNotNone(harvest.result)

    def test_partial_recovers_verdict_codes_and_missing_sections(self):
        harvest = harvest_reflection(
            """AdaMAST reflection:
- Checkpoint ID: cp-1
- Observe: checked everything
- Map:
  - none apply | considered: MAST-12 | evidence: "suite green"
- Decide: no change needed, because it is verified
Final AdaMAST status: READY_TO_SUBMIT
""",
            checkpoint_id="cp-1",
            known_code_ids=("MAST-12",),
        )
        self.assertIsNone(harvest.result)
        partial = harvest.partial
        self.assertTrue(partial.has_block)
        self.assertIn("correlate", partial.missing_sections)
        self.assertEqual(partial.status, "READY_TO_SUBMIT")
        self.assertIs(partial.decide_change, False)
        self.assertIn("MAST-12", partial.mentioned_codes)
        self.assertIn("empty or missing Correlate step", partial.issues)

    def test_ambiguous_statuses_do_not_yield_a_pinnable_status(self):
        harvest = harvest_reflection(
            """AdaMAST reflection:
- Checkpoint ID: cp-1
- Observe: checked
Final AdaMAST status: READY_TO_SUBMIT
Final decision: repair
""",
            checkpoint_id="cp-1",
            known_code_ids=("MAST-12",),
        )
        self.assertIsNone(harvest.result)
        self.assertGreater(len(set(harvest.partial.statuses)), 1)
        self.assertIsNone(harvest.partial.status)

    def test_no_block_yields_empty_partial(self):
        harvest = harvest_reflection(
            "all done.",
            checkpoint_id="cp-1",
            known_code_ids=("MAST-12",),
        )
        self.assertIsNone(harvest.result)
        self.assertFalse(harvest.partial.has_block)
        self.assertIn("missing `AdaMAST reflection` block", harvest.error)


if __name__ == "__main__":
    unittest.main()
