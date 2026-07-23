"""Tests for the built-in MAST floor constant."""

import unittest
from pathlib import Path

from adamast.core import mast, store

STORE_DIR = Path(__file__).resolve().parent / "fixtures" / "taxonomies"

# Expected 14 modes: id -> (name, category). From Cemri et al. (2025).
EXPECTED = {
    "MAST-1": ("Disobedient to task specification", "Specification"),
    "MAST-2": ("Disobedient to role specification", "Specification"),
    "MAST-3": ("Step repetition", "Specification"),
    "MAST-4": ("Loss of conversation history", "Specification"),
    "MAST-5": ("Unaware of termination conditions", "Specification"),
    "MAST-6": ("Conversation reset", "Coordination"),
    "MAST-7": ("Failure to ask for clarification", "Coordination"),
    "MAST-8": ("Task derailment", "Coordination"),
    "MAST-9": ("Information withholding", "Coordination"),
    "MAST-10": ("Ignored other agent's input", "Coordination"),
    "MAST-11": ("Premature termination", "Verification"),
    "MAST-12": ("No or incomplete verification", "Verification"),
    "MAST-13": ("Weak verification", "Verification"),
    "MAST-14": ("Incorrect verification", "Verification"),
}

# Wording that must NOT survive: action directives, workflow/gate framing,
# and benchmark-specific terms that should have been generalized.
FORBIDDEN_SUBSTRINGS = [
    "AVOID", "DO ", "Workflow", "pre-submission", "checklist", "gate",
    "submit", "patch", "diff", "issue", "hidden test", "grep",
    "SWE-bench", "reproducer",
]


class MastShapeTests(unittest.TestCase):
    def test_is_a_taxonomy_record(self):
        self.assertEqual(mast.MAST["taxonomy_id"], "mast")
        self.assertIn("repo", mast.MAST)
        self.assertIn("domain", mast.MAST)
        self.assertIn("codes", mast.MAST)

    def test_has_all_14_modes(self):
        self.assertEqual(len(mast.MAST["codes"]), 14)

    def test_ids_names_categories_match(self):
        by_id = {c["id"]: c for c in mast.MAST["codes"]}
        self.assertEqual(set(by_id), set(EXPECTED))
        for mode_id, (name, category) in EXPECTED.items():
            self.assertEqual(by_id[mode_id]["name"], name, mode_id)
            self.assertEqual(by_id[mode_id]["category"], category, mode_id)

    def test_category_counts(self):
        cats = [c["category"] for c in mast.MAST["codes"]]
        self.assertEqual(cats.count("Specification"), 5)
        self.assertEqual(cats.count("Coordination"), 5)
        self.assertEqual(cats.count("Verification"), 4)

    def test_every_mode_has_the_four_fields(self):
        for c in mast.MAST["codes"]:
            self.assertEqual(set(c.keys()), {"id", "name", "description", "category"})

    def test_descriptions_are_nonempty(self):
        for c in mast.MAST["codes"]:
            self.assertTrue(c["description"].strip(), c["id"])


class MastContentStrippedTests(unittest.TestCase):
    def test_no_directive_or_workflow_or_benchmark_wording(self):
        blob = " ".join(c["description"] for c in mast.MAST["codes"])
        for bad in FORBIDDEN_SUBSTRINGS:
            self.assertNotIn(bad, blob, f"description text still contains {bad!r}")


class MastNotInStoreTests(unittest.TestCase):
    def test_mast_not_in_list_all(self):
        ids = {r["taxonomy_id"] for r in store.list_all(STORE_DIR)}
        self.assertNotIn("mast", ids)

    def test_mast_not_fetchable_from_store(self):
        self.assertFalse(store.exists("mast", STORE_DIR))


if __name__ == "__main__":
    unittest.main()
