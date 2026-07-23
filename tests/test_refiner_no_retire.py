"""Refiner mutation policy: MERGE consolidates, outright RETIRE is blocked."""

from __future__ import annotations

import unittest

from adamast.learning.reflection_refinement import _apply_proposals
from adamast.core.taxonomy_data import Taxonomy


def _tax() -> Taxonomy:
    return Taxonomy.from_flat({
        "repo": "r", "domain": "d",
        "codes": [
            {"id": "C.1", "name": "Regex_Special_Char", "description": "x", "category": "C"},
            {"id": "C.2", "name": "Incorrect_Regex_Pattern", "description": "y", "category": "C"},
            {"id": "C.3", "name": "Missing_Edge_Cases", "description": "z", "category": "C"},
        ],
    })


class ApplyProposalsPolicy(unittest.TestCase):
    def test_retire_blocked_by_default(self) -> None:
        tax = _tax()
        applied = _apply_proposals(tax, {"retire": [{"code": "C.3", "reason": "unused"}]})
        self.assertEqual(applied["retire"], [])
        self.assertIn("Missing_Edge_Cases", [c.name for c in tax.codes])

    def test_retire_honored_when_allowed(self) -> None:
        tax = _tax()
        applied = _apply_proposals(
            tax, {"retire": [{"code": "C.3", "reason": "unused"}]}, allow_retire=True
        )
        self.assertEqual(len(applied["retire"]), 1)
        self.assertNotIn("Missing_Edge_Cases", [c.name for c in tax.codes])

    def test_merge_consolidates_sources_into_general_code(self) -> None:
        tax = _tax()
        applied = _apply_proposals(tax, {
            "merge": [{
                "codes": ["C.1", "C.2"],
                "category": "C",
                "name": "Incorrect_Pattern_Logic",
                "definition": "wrong or unescaped pattern construction",
            }],
        })
        names = [c.name for c in tax.codes]
        self.assertEqual(len(applied["merge"]), 1)
        self.assertIn("Incorrect_Pattern_Logic", names)
        self.assertNotIn("Regex_Special_Char", names)
        self.assertNotIn("Incorrect_Regex_Pattern", names)
        self.assertIn("Missing_Edge_Cases", names)          # untouched survivor
        self.assertEqual(len(tax.codes), 2)
        # The merged code is a new concept with a fresh id; the retired
        # sources' ids (C.1, C.2) keep their evidence and are never reused.
        merged = next(c for c in tax.codes if c.name == "Incorrect_Pattern_Logic")
        self.assertEqual(merged.code, "C.4")
        survivor = next(c for c in tax.codes if c.name == "Missing_Edge_Cases")
        self.assertEqual(survivor.code, "C.3")

    def test_merge_requires_two_valid_sources(self) -> None:
        tax = _tax()
        applied = _apply_proposals(tax, {
            "merge": [{"codes": ["C.1", "C.99"], "name": "Bogus", "definition": "n"}],
        })
        self.assertEqual(applied["merge"], [])
        self.assertEqual(len(tax.codes), 3)


if __name__ == "__main__":
    unittest.main()
