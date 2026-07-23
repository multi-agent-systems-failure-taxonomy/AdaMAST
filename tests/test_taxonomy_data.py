"""Tests for the ported Taxonomy data model in adamast.core.taxonomy_data."""

import json
import tempfile
import unittest
from pathlib import Path

from adamast.core.taxonomy_data import (
    Code,
    CostMeter,
    JudgeLog,
    Taxonomy,
    render_code_spec,
)


FLAT_FIXTURE = {
    "repo": "demo",
    "domain": "test",
    "codes": [
        {"id": "A.1", "name": "Loop", "description": "Agent loops indefinitely", "category": "A"},
        {"id": "B.1", "name": "BadSolver", "description": "Solver fails", "category": "B",
         "applies_to_role": "solver"},
        {"id": "C.1", "name": "BadMath", "description": "Off-by-one", "category": "C"},
    ],
}

ADAMAST_FIXTURE = {
    "annotation_layer": {
        "category_a": [
            {"code": "A.1", "name": "Loop", "definition": "Agent loops",
             "severity": "major"},
        ],
        "category_b": [],
        "category_c": [
            {"code": "C.1", "name": "BadMath", "definition": "Off-by-one",
             "severity": "minor"},
        ],
    },
    "full_layer": {
        "category_a": {
            "A.1": {"code": "A.1", "name": "Loop", "definition": "Agent loops",
                    "severity": "major", "when_to_use": "tight repeats",
                    "detection_heuristics": ["repeated tool call"], "origin": "seed"},
        },
        "category_b": {},
        "category_c": {
            "C.1": {"code": "C.1", "name": "BadMath", "definition": "Off-by-one",
                    "severity": "minor", "detection_heuristics": [], "origin": "seed"},
        },
    },
    "role_definitions": {"solver": {"agents": ["step1"]}},
}


class TaxonomyConstructorTests(unittest.TestCase):
    def test_from_flat_round_trips_codes(self) -> None:
        tax = Taxonomy.from_flat(FLAT_FIXTURE)
        self.assertEqual(len(tax.codes), 3)
        self.assertEqual(tax.codes[0].category, "A")
        self.assertEqual(tax.codes[1].applies_to_role, "solver")
        # Supplied ids are preserved verbatim, in order.
        self.assertEqual([c.code for c in tax.codes], ["A.1", "B.1", "C.1"])

    def test_from_dict_reads_adamast_layers(self) -> None:
        tax = Taxonomy.from_dict(ADAMAST_FIXTURE)
        self.assertEqual(len(tax.codes), 2)
        a = next(c for c in tax.codes if c.category == "A")
        self.assertEqual(a.detection_heuristics, ["repeated tool call"])
        self.assertEqual(a.when_to_use, "tight repeats")
        self.assertEqual(tax.roles(), ["solver"])

    def test_from_json_loads_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tax.json"
            path.write_text(json.dumps(ADAMAST_FIXTURE))
            tax = Taxonomy.from_json(path)
            self.assertEqual(len(tax.codes), 2)
            self.assertEqual(tax.metadata.get("seed_path"), str(path))


class TaxonomyMutationTests(unittest.TestCase):
    def test_retire_leaves_gap_and_never_reuses_the_id(self) -> None:
        tax = Taxonomy.from_flat(FLAT_FIXTURE)
        a_uid = next(c.uid for c in tax.codes if c.category == "A")
        tax.retire(a_uid)
        self.assertEqual({c.code for c in tax.codes}, {"B.1", "C.1"})
        # A.1's recorded history stays its own: the next A code is fresh.
        tax.add("A", {"name": "NewA", "definition": "..."})
        codes = [c.code for c in tax.codes if c.category == "A"]
        self.assertEqual(codes, ["A.2"])

    def test_split_preserves_parent_uid(self) -> None:
        tax = Taxonomy.from_flat(FLAT_FIXTURE)
        c_uid = next(c.uid for c in tax.codes if c.category == "C")
        new_uids = tax.split(c_uid, [
            {"name": "BadMathA", "definition": "off by one in loop"},
            {"name": "BadMathB", "definition": "off by one in slice"},
        ])
        c_codes = [c for c in tax.codes if c.category == "C"]
        self.assertEqual(len(c_codes), 2)
        self.assertEqual(len(new_uids), 1)
        self.assertEqual(c_codes[0].uid, c_uid)
        self.assertEqual(c_codes[0].origin, "split")
        self.assertEqual(c_codes[1].parent_uid, c_uid)

    def test_add_appends_with_the_next_free_id(self) -> None:
        tax = Taxonomy.from_flat(FLAT_FIXTURE)
        original_count = len(tax.codes)
        uid = tax.add("A", {"name": "NewA", "definition": "..."})
        self.assertEqual(len(tax.codes), original_count + 1)
        a_codes = [c for c in tax.codes if c.category == "A"]
        self.assertEqual(a_codes[-1].uid, uid)
        self.assertEqual(a_codes[-1].code, "A.2")
        self.assertEqual(a_codes[0].code, "A.1")  # existing id untouched


class IdStabilityTests(unittest.TestCase):
    """Code ids are identities: assigned once, never rewritten, never reused.

    Evidence records, recorded checkpoints, and human notes all join on the
    id, so a registered taxonomy's ids (sparse dotted, MAST-shaped, or bare
    numeric) must survive construction and every mutation unchanged.
    """

    SPARSE = {
        "repo": "paper",
        "domain": "doc-qa",
        "codes": [
            {"id": "A.4", "name": "Task_Refusal", "description": "gives up",
             "category": "A"},
            {"id": "A.10", "name": "Noncompliance", "description": "format",
             "category": "A"},
            {"id": "MAST-12", "name": "Verifier_Skipped", "description": "no check",
             "category": "B"},
            {"id": "7", "name": "Bare_Numeric", "description": "legacy id",
             "category": "C"},
        ],
    }

    def test_registered_ids_survive_construction(self) -> None:
        tax = Taxonomy.from_flat(self.SPARSE)
        self.assertEqual([c.code for c in tax.codes], ["A.4", "A.10", "MAST-12", "7"])

    def test_missing_id_is_assigned_above_the_high_water_mark(self) -> None:
        flat = {**self.SPARSE, "codes": [
            *self.SPARSE["codes"],
            {"name": "Fresh", "description": "new mode", "category": "A"},
        ]}
        tax = Taxonomy.from_flat(flat)
        self.assertEqual(tax.codes[-1].code, "A.11")

    def test_duplicate_id_is_reassigned_not_silently_shared(self) -> None:
        tax = Taxonomy.from_flat({"repo": "r", "domain": "d", "codes": [
            {"id": "A.4", "name": "One", "description": "x", "category": "A"},
            {"id": "A.4", "name": "Two", "description": "y", "category": "A"},
        ]})
        self.assertEqual([c.code for c in tax.codes], ["A.4", "A.5"])

    def test_persisted_high_water_prevents_id_resurrection(self) -> None:
        # This taxonomy once reached A.12; those codes were retired before
        # the round-trip. A new code must not reclaim a retired id.
        tax = Taxonomy.from_flat({
            "repo": "r", "domain": "d", "id_high_water": {"A": 12},
            "codes": [
                {"id": "A.4", "name": "Kept", "description": "x", "category": "A"},
            ],
        })
        tax.add("A", {"name": "Fresh", "definition": "new"})
        self.assertEqual([c.code for c in tax.codes], ["A.4", "A.13"])

    def test_high_water_mark_is_republished_in_metadata(self) -> None:
        tax = Taxonomy.from_flat(self.SPARSE)
        self.assertEqual(tax.metadata["id_high_water"], {"A": 10})
        tax.add("C", {"name": "Fresh", "definition": "new"})
        self.assertEqual(tax.metadata["id_high_water"], {"A": 10, "C": 1})

    def test_split_children_mint_fresh_ids_and_parent_keeps_its_id(self) -> None:
        tax = Taxonomy.from_flat(self.SPARSE)
        parent_uid = tax.codes[0].uid  # A.4
        tax.split(parent_uid, [
            {"name": "Refusal_Hard", "definition": "quits outright"},
            {"name": "Refusal_Soft", "definition": "hedges instead"},
        ])
        a_ids = [c.code for c in tax.codes if c.category == "A"]
        self.assertEqual(a_ids, ["A.4", "A.11", "A.10"])


class RenderCodeSpecTests(unittest.TestCase):
    def test_includes_definition_and_heuristics(self) -> None:
        c = Code(uid="u1", category="A", code="A.1", name="Loop",
                 definition="Agent loops", when_to_use="when tight repeats",
                 detection_heuristics=["repeats tool call"])
        rendered = render_code_spec(c)
        self.assertIn("A.1: Loop", rendered)
        self.assertIn("Agent loops", rendered)
        self.assertIn("when tight repeats", rendered)
        self.assertIn("repeats tool call", rendered)


class JudgeLogTests(unittest.TestCase):
    def test_lifetime_and_window_track_independently(self) -> None:
        log = JudgeLog()
        log.record("u1")
        log.record("u1")
        log.record("u2")
        self.assertEqual(log.lifetime, {"u1": 2, "u2": 1})
        self.assertEqual(log.window, {"u1": 2, "u2": 1})
        log.close_window(["u1", "u2", "u3"])
        self.assertEqual(log.window, {})
        # u3 was never fired -> consecutive_unused goes to 1.
        self.assertEqual(log.consecutive_unused.get("u3"), 1)

    def test_retirement_candidates_require_lifetime_zero_and_streak(self) -> None:
        log = JudgeLog()
        codes = [Code(uid="u1", category="A", code="A.1", name="x", definition=""),
                 Code(uid="u2", category="A", code="A.2", name="y", definition="")]
        log.record("u1")
        log.close_window(["u1", "u2"])
        log.close_window(["u1", "u2"])
        self.assertEqual(log.retirement_candidates(codes, min_consecutive=2), ["u2"])
        self.assertEqual(log.retirement_candidates(codes, min_consecutive=3), [])


class CostMeterTests(unittest.TestCase):
    def test_add_extra_accumulates(self) -> None:
        m = CostMeter()
        m.add_extra(0.5)
        m.add_extra(1.25)
        self.assertAlmostEqual(m.total(), 1.75)

    def test_invalid_inputs_are_swallowed(self) -> None:
        m = CostMeter()
        m.add_extra(None)  # type: ignore[arg-type]
        m.add_extra("not a number")  # type: ignore[arg-type]
        self.assertEqual(m.total(), 0.0)


if __name__ == "__main__":
    unittest.main()
