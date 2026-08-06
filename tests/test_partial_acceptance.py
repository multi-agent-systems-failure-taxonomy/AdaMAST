"""F2.1 partial acceptance: keep the supported code subset, drop the rest."""

import unittest

from adamast.learning.learning_jobs import (
    MIN_SUPPORTED_CODES,
    LearningJobError,
    _attach_support_review,
    validate_support_review,
)


def _candidate(ids):
    return {
        "codes": [
            {
                "id": cid,
                "name": f"Code {cid}",
                "description": "d",
                "category": "C",
                "evidence": {"trace_ids": ["t1", "t2"], "quotes": []},
            }
            for cid in ids
        ]
    }


def _snapshot():
    return {"traces": [{"problem_id": "t1"}, {"problem_id": "t2"}]}


def _row(cid, supported):
    return {"id": cid, "supported": supported, "reason": "r", "trace_ids": ["t1"]}


def _review(rows):
    return {"supported": True, "codes": rows}


class PartialAcceptanceTests(unittest.TestCase):
    def test_supported_subset_survives_and_unsupported_are_dropped(self):
        candidate = _candidate(["c1", "c2", "c3", "c4"])
        review = _review(
            [
                _row("c1", True),
                _row("c2", True),
                _row("c3", True),
                _row("c4", False),
            ]
        )
        validated = validate_support_review(review, candidate, _snapshot())
        self.assertTrue(validated["supported"])
        self.assertEqual(
            {row["id"] for row in validated["codes"]}, {"c1", "c2", "c3"}
        )
        # Attaching prunes the candidate to the supported subset and records
        # each surviving code's review row.
        _attach_support_review(candidate, validated)
        self.assertEqual({c["id"] for c in candidate["codes"]}, {"c1", "c2", "c3"})
        for code in candidate["codes"]:
            self.assertIn(
                "independent_support_review",
                code["evidence"]["validation"],
            )

    def test_exactly_the_floor_is_accepted(self):
        self.assertEqual(MIN_SUPPORTED_CODES, 3)
        candidate = _candidate(["c1", "c2", "c3", "c4", "c5"])
        review = _review(
            [
                _row("c1", True),
                _row("c2", True),
                _row("c3", True),
                _row("c4", False),
                _row("c5", False),
            ]
        )
        validated = validate_support_review(review, candidate, _snapshot())
        self.assertEqual(len(validated["codes"]), 3)

    def test_below_the_floor_is_rejected(self):
        candidate = _candidate(["c1", "c2", "c3", "c4"])
        review = _review(
            [
                _row("c1", True),
                _row("c2", True),
                _row("c3", False),
                _row("c4", False),
            ]
        )
        with self.assertRaisesRegex(LearningJobError, "too few supported codes"):
            validate_support_review(review, candidate, _snapshot())

    def test_incomplete_review_still_rejected(self):
        # The completeness gate is unchanged: every candidate code must be
        # reviewed, even under partial acceptance.
        candidate = _candidate(["c1", "c2", "c3", "c4"])
        review = _review([_row("c1", True), _row("c2", True), _row("c3", True)])
        with self.assertRaisesRegex(LearningJobError, "omitted candidate code"):
            validate_support_review(review, candidate, _snapshot())


if __name__ == "__main__":
    unittest.main()
