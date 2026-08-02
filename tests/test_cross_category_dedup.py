"""Regression coverage for Step 6 cross-category deduplication.

The model is shown a schema with a single-string ``"remove"`` field, but when
one concept spans several codes it emits a list instead. That list used to hit
``set.add`` unhashed, and the batch-wide exception handler then discarded every
identified duplicate.
"""

import json
import unittest

from adamast.learning.pipeline.draft import CrossCategoryDeduplicator


class _CannedClient:
    def __init__(self, payload: dict):
        self._payload = payload

    def complete(self, prompt: str, system: str = "") -> str:
        return json.dumps(self._payload)


def _codes(*ids: str) -> list[dict]:
    return [
        {"code": code, "name": f"Name {code}", "definition": f"Definition {code}"}
        for code in ids
    ]


class CrossCategoryDeduplicatorTests(unittest.TestCase):
    def deduplicate(self, duplicates) -> dict:
        client = _CannedClient({"duplicates_found": duplicates})
        return CrossCategoryDeduplicator(client).deduplicate(
            _codes("A.1", "A.2"), _codes("B.3"), _codes("C.4")
        )

    def test_string_remove_still_filters(self):
        result = self.deduplicate(
            [{"concept": "x", "keep_in": "A.1", "remove": "B.3"}]
        )
        self.assertEqual([c["code"] for c in result["category_b"]], [])
        self.assertEqual(len(result["duplicates_found"]), 1)

    def test_list_remove_filters_every_named_code(self):
        result = self.deduplicate(
            [{"concept": "x", "keep_in": "A.1", "remove": ["B.3", "C.4"]}]
        )
        self.assertEqual([c["code"] for c in result["category_b"]], [])
        self.assertEqual([c["code"] for c in result["category_c"]], [])
        self.assertEqual([c["code"] for c in result["category_a"]], ["A.1", "A.2"])
        # The except-path fallback would have reported no duplicates at all.
        self.assertEqual(len(result["duplicates_found"]), 1)

    def test_malformed_entry_does_not_void_the_batch(self):
        result = self.deduplicate(
            [
                "not a dict",
                {"concept": "x", "keep_in": "A.1", "remove": {"code": "B.3"}},
                {"concept": "y", "keep_in": "A.1", "remove": ["C.4", None, ""]},
            ]
        )
        self.assertEqual([c["code"] for c in result["category_b"]], ["B.3"])
        self.assertEqual([c["code"] for c in result["category_c"]], [])
        self.assertEqual(len(result["duplicates_found"]), 3)

    def test_keeper_survives_even_when_listed_for_removal(self):
        result = self.deduplicate(
            [
                {
                    "concept": "x",
                    "found_in": ["A.1", "B.3", "C.4"],
                    "keep_in": "A.1",
                    "remove": ["A.1", "B.3", "C.4"],
                }
            ]
        )
        self.assertEqual([c["code"] for c in result["category_a"]], ["A.1", "A.2"])
        self.assertEqual([c["code"] for c in result["category_b"]], [])
        self.assertEqual([c["code"] for c in result["category_c"]], [])

    def test_entry_removing_its_whole_concept_without_a_keeper_is_skipped(self):
        result = self.deduplicate(
            [
                {
                    "concept": "x",
                    "found_in": ["B.3", "C.4"],
                    "remove": ["B.3", "C.4"],
                }
            ]
        )
        self.assertEqual([c["code"] for c in result["category_b"]], ["B.3"])
        self.assertEqual([c["code"] for c in result["category_c"]], ["C.4"])

    def test_one_entrys_keeper_beats_another_entrys_removal(self):
        result = self.deduplicate(
            [
                {"concept": "x", "found_in": ["A.1", "B.3"], "keep_in": "A.1",
                 "remove": "B.3"},
                {"concept": "y", "found_in": ["C.4", "A.1"], "keep_in": "C.4",
                 "remove": "A.1"},
            ]
        )
        # Removing A.1 would wipe concept x entirely; the keeper wins.
        self.assertEqual([c["code"] for c in result["category_a"]], ["A.1", "A.2"])
        self.assertEqual([c["code"] for c in result["category_b"]], [])
        self.assertEqual([c["code"] for c in result["category_c"]], ["C.4"])

    def test_hallucinated_keeper_cannot_license_wiping_a_concept(self):
        # keep_in names a code that does not exist (a typo of B.3), while
        # remove covers every real copy. Nothing may be removed.
        result = self.deduplicate(
            [
                {"concept": "x", "found_in": ["B.3", "C.4"], "keep_in": "B.33",
                 "remove": ["B.3", "C.4"]},
            ]
        )
        self.assertEqual([c["code"] for c in result["category_b"]], ["B.3"])
        self.assertEqual([c["code"] for c in result["category_c"]], ["C.4"])

    def test_hallucinated_keeper_with_partial_removal_still_dedupes(self):
        # The same fake keeper is harmless when a real copy survives anyway.
        result = self.deduplicate(
            [
                {"concept": "x", "found_in": ["B.3", "C.4"], "keep_in": "B.33",
                 "remove": "C.4"},
            ]
        )
        self.assertEqual([c["code"] for c in result["category_b"]], ["B.3"])
        self.assertEqual([c["code"] for c in result["category_c"]], [])

    def test_removals_split_across_entries_cannot_wipe_a_concept(self):
        # Neither keeperless entry removes everything it found on its own,
        # but together they would; one code must be rescued.
        result = self.deduplicate(
            [
                {"concept": "x", "found_in": ["B.3", "C.4"], "remove": "B.3"},
                {"concept": "x", "found_in": ["B.3", "C.4"], "remove": "C.4"},
            ]
        )
        survivors = [
            c["code"]
            for key in ("category_b", "category_c")
            for c in result[key]
        ]
        self.assertEqual(len(survivors), 1)

    def test_full_batch_removes_every_marked_code(self):
        # A realistic messy batch: string and list forms, padded whitespace,
        # and a code the model hallucinated. Every named real code must go;
        # nothing else may.
        client = _CannedClient(
            {
                "duplicates_found": [
                    {"concept": "w", "found_in": ["A.1", "B.3"], "keep_in": "A.1",
                     "remove": "B.3"},
                    {"concept": "x", "found_in": ["A.2", "C.4"], "keep_in": "A.2",
                     "remove": [" C.4 "]},
                    {"concept": "y", "found_in": ["A.1", "D.9"], "keep_in": "A.1",
                     "remove": "D.9"},
                ]
            }
        )
        result = CrossCategoryDeduplicator(client).deduplicate(
            _codes("A.1", "A.2"), _codes("B.3"), _codes("C.4")
        )
        self.assertEqual([c["code"] for c in result["category_a"]], ["A.1", "A.2"])
        self.assertEqual(result["category_b"], [])
        self.assertEqual(result["category_c"], [])
        # Survivors keep their full payload, not the summarized form.
        self.assertIn("definition", result["category_a"][0])

    def test_provider_failure_still_returns_codes_unfiltered(self):
        class _FailingClient:
            def complete(self, prompt: str, system: str = "") -> str:
                raise RuntimeError("provider down")

        result = CrossCategoryDeduplicator(_FailingClient()).deduplicate(
            _codes("A.1"), _codes("B.3"), _codes("C.4")
        )
        self.assertEqual([c["code"] for c in result["category_a"]], ["A.1"])
        self.assertEqual([c["code"] for c in result["category_b"]], ["B.3"])
        self.assertEqual(result["duplicates_found"], [])


if __name__ == "__main__":
    unittest.main()
