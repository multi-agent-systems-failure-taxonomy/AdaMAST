"""Compact-checkpoint code citations across every real taxonomy id shape.

Regression tests for the defect where the compact transports could never
record a cited code: the known-code map read the wrong key (``code_id``
instead of the registered ``id``) and the citation tokenizer only accepted
hyphen-digit ids (``MAST-12``), rejecting the dotted (``A.1``) and bare
numeric (``1``) ids that registered taxonomies actually use.
"""

from __future__ import annotations

import unittest

from adamast.hosts.claude_code.checkpoint import _compact_reflection
from adamast.hosts.codex.runtime import _harvest_codex_checkpoint
from adamast.core.reflection import mentioned_codes


def compact(codes: str, next_action: str = "no further action required") -> str:
    return (
        "Checkpoint: FINAL_GATE\n"
        f"Relevant codes: {codes}\n"
        "Evidence: the verifier accepted a wrong answer at step 3\n"
        f"Next action: {next_action}\n"
    )


def state_with(codes: list[dict]) -> dict:
    return {"taxonomy": {"codes": codes}, "episode_sequence": 1}


DOTTED = state_with(
    [
        {"id": "A.1", "name": "Context exhaustion", "category": "A"},
        {"id": "A.10", "name": "Aggregation error", "category": "A"},
        {"id": "B.2", "name": "Checker rubber-stamps", "category": "B"},
    ]
)
NUMERIC = state_with(
    [
        {"id": "1", "name": "N+1 query", "category": "A"},
        {"id": "12", "name": "Stale cache", "category": "A"},
    ]
)
MAST_SHAPED = state_with(
    [
        {"id": "MAST-1", "name": "Disobedient to task specification"},
        {"id": "MAST-12", "name": "Weak verification"},
    ]
)
LEGACY_KEY = state_with([{"code_id": "ctx-1", "name": "Context loss"}])


class MentionedCodesTests(unittest.TestCase):
    def test_dotted_id_never_matches_inside_longer_id(self):
        self.assertEqual(mentioned_codes("A.10", ["A.1", "A.10"]), ("A.10",))
        self.assertEqual(mentioned_codes("A.1", ["A.1", "A.10"]), ("A.1",))

    def test_hyphen_and_numeric_shapes_match(self):
        self.assertEqual(mentioned_codes("MAST-12", ["MAST-1", "MAST-12"]), ("MAST-12",))
        self.assertEqual(mentioned_codes("1", ["1", "12"]), ("1",))
        self.assertEqual(mentioned_codes("12", ["1", "12"]), ("12",))

    def test_result_preserves_known_order_not_citation_order(self):
        self.assertEqual(
            mentioned_codes("B.2 then A.1", ["A.1", "A.10", "B.2"]),
            ("A.1", "B.2"),
        )


class ClaudeCompactCitationTests(unittest.TestCase):
    def test_dotted_ids_are_recorded(self):
        reflection, status, error = _compact_reflection(
            compact("A.1, B.2"), DOTTED, gate="stop"
        )
        self.assertIsNotNone(reflection, error)
        self.assertEqual(
            [a.code_id for a in reflection.assignments], ["A.1", "B.2"]
        )
        self.assertFalse(reflection.none_apply)
        self.assertEqual(status, "READY_TO_SUBMIT")

    def test_dotted_boundary_is_exact(self):
        reflection, _, error = _compact_reflection(
            compact("A.10"), DOTTED, gate="stop"
        )
        self.assertIsNotNone(reflection, error)
        self.assertEqual([a.code_id for a in reflection.assignments], ["A.10"])

    def test_bare_numeric_store_ids_are_recorded(self):
        reflection, _, error = _compact_reflection(
            compact("1"), NUMERIC, gate="stop"
        )
        self.assertIsNotNone(reflection, error)
        self.assertEqual([a.code_id for a in reflection.assignments], ["1"])

    def test_mast_floor_ids_are_recorded(self):
        reflection, _, error = _compact_reflection(
            compact("MAST-12"), MAST_SHAPED, gate="stop"
        )
        self.assertIsNotNone(reflection, error)
        self.assertEqual(
            [a.code_id for a in reflection.assignments], ["MAST-12"]
        )

    def test_legacy_code_id_key_still_accepted(self):
        reflection, _, error = _compact_reflection(
            compact("ctx-1"), LEGACY_KEY, gate="stop"
        )
        self.assertIsNotNone(reflection, error)
        self.assertEqual([a.code_id for a in reflection.assignments], ["ctx-1"])

    def test_unknown_citation_without_none_apply_is_rejected(self):
        reflection, status, error = _compact_reflection(
            compact("Z.9"), DOTTED, gate="stop"
        )
        self.assertIsNone(reflection)
        self.assertEqual(status, "MISSING_CHECKPOINT")
        self.assertIn("active taxonomy code", error)

    def test_citation_with_repair_next_action_requires_repair(self):
        reflection, status, error = _compact_reflection(
            compact("A.1", next_action="repair the verifier prompt"),
            DOTTED,
            gate="stop",
        )
        self.assertIsNotNone(reflection, error)
        self.assertEqual(status, "REPAIR_REQUIRED")


class CodexCompactCitationTests(unittest.TestCase):
    def test_dotted_ids_are_recorded(self):
        reflection, status, error = _harvest_codex_checkpoint(
            compact("A.1, B.2"), DOTTED, gate="stop"
        )
        self.assertIsNotNone(reflection, error)
        self.assertEqual(
            [a.code_id for a in reflection.assignments], ["A.1", "B.2"]
        )
        self.assertEqual(status, "READY_TO_SUBMIT")

    def test_bare_numeric_store_ids_are_recorded(self):
        reflection, _, error = _harvest_codex_checkpoint(
            compact("12"), NUMERIC, gate="stop"
        )
        self.assertIsNotNone(reflection, error)
        self.assertEqual([a.code_id for a in reflection.assignments], ["12"])

    def test_assignments_follow_taxonomy_order(self):
        reflection, _, error = _harvest_codex_checkpoint(
            compact("B.2 and also A.1"), DOTTED, gate="stop"
        )
        self.assertIsNotNone(reflection, error)
        self.assertEqual(
            [a.code_id for a in reflection.assignments], ["A.1", "B.2"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
