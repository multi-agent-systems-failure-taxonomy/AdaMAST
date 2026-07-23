"""Tests for adamast-register-taxonomy — the no-rejudge entry point.

The whole point of this CLI is: you have a taxonomy.json from somewhere
(custom pipeline, hand-edited, sibling project) and want to make it
inheritable in the adamast store WITHOUT re-running judges or
generation. These tests exercise the no-traces happy paths, the
optional refinement path (with a stub LLM), batch register, --replace,
and the validation guards.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adamast.learning.register_taxonomy import (
    RegisteredTaxonomyResult,
    _expand_paths,
    load_candidate,
    register_taxonomy_file,
    register_taxonomy_files,
)
from adamast.core import store

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL_ADAMAST_OUTPUT = FIXTURES / "real_adamast_generation_output.json"

FLAT = {
    "repo": "demo",
    "domain": "test",
    "codes": [
        {"id": "A.1", "name": "Loop", "description": "agent loops",
         "category": "A", "severity": "major"},
        {"id": "C.1", "name": "BadMath", "description": "off-by-one",
         "category": "C", "severity": "minor"},
    ],
}

ADAMAST_SHAPE = {
    "annotation_layer": {
        "category_a": [{"code": "A.1", "name": "Loop", "definition": "loops"}],
        "category_b": [],
        "category_c": [{"code": "C.1", "name": "Math", "definition": "off-by-one"}],
    },
    "full_layer": {
        "category_a": {"A.1": {"code": "A.1", "name": "Loop", "definition": "loops"}},
        "category_b": {},
        "category_c": {"C.1": {"code": "C.1", "name": "Math", "definition": "off-by-one"}},
    },
}


class LoadCandidateTests(unittest.TestCase):
    def test_loads_flat_format(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.json"
            p.write_text(json.dumps(FLAT))
            c = load_candidate(p)
            self.assertEqual(len(c["codes"]), 2)
            self.assertEqual(c["repo"], "demo")

    def test_loads_adamast_pipeline_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.json"
            p.write_text(json.dumps(ADAMAST_SHAPE))
            c = load_candidate(p)
            # Round-trip via Taxonomy: 2 codes survive with their ids intact.
            self.assertEqual({c["category"] for c in c["codes"]}, {"A", "C"})
            self.assertEqual([code["id"] for code in c["codes"]], ["A.1", "C.1"])

    def test_loads_real_adamast_pipeline_shape_with_annotation_codes(self) -> None:
        c = load_candidate(REAL_ADAMAST_OUTPUT)
        self.assertEqual({code["id"] for code in c["codes"]}, {"A.1", "B.1"})
        self.assertEqual(c["domain"], "Software Engineering / Code Repair")

    def test_rejects_unrecognized_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.json"
            p.write_text(json.dumps({"unknown": "shape"}))
            with self.assertRaises(ValueError):
                load_candidate(p)

    def test_sparse_ids_and_high_water_survive_registration(self) -> None:
        # A registered taxonomy's ids are identities; the persisted id floor
        # rides through load_candidate -> record so retired ids never return.
        sparse = {
            "repo": "demo", "domain": "test", "id_high_water": {"A": 12},
            "codes": [
                {"id": "A.4", "name": "Refusal", "description": "gives up",
                 "category": "A", "severity": "major"},
                {"id": "A.10", "name": "Noncompliance", "description": "format",
                 "category": "A", "severity": "minor"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.json"
            p.write_text(json.dumps(sparse))
            result = register_taxonomy_file(
                p,
                store_dir=Path(td) / "store",
                trace_root=Path(td) / "traces",
            )
            record = json.loads(result.taxonomy_path.read_text())
        self.assertEqual([c["id"] for c in record["codes"]], ["A.4", "A.10"])
        self.assertEqual(record["id_high_water"], {"A": 12})


class RegisterTaxonomyFileTests(unittest.TestCase):
    """The core no-rejudge path: file in, registered id out."""

    def test_registers_flat_file_without_traces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "t.json"
            src.write_text(json.dumps(FLAT))
            r = register_taxonomy_file(src, store_dir=td / "store")
            self.assertTrue(r.taxonomy_id.startswith("tax-"))
            self.assertEqual(r.trace_count, 0)
            self.assertFalse(r.refinement["applied"])
            self.assertTrue(r.taxonomy_path.is_file())

    def test_registers_utf8_bom_taxonomy_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "t.json"
            src.write_text(json.dumps(FLAT), encoding="utf-8-sig")
            r = register_taxonomy_file(
                src,
                store_dir=td / "store",
                taxonomy_id="bom-import",
            )
            rec = store.fetch_by_id(r.taxonomy_id, td / "store")
            self.assertEqual(rec["taxonomy_id"], "bom-import")

    def test_registers_adamast_pipeline_file_without_traces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "t.json"
            src.write_text(json.dumps(ADAMAST_SHAPE))
            r = register_taxonomy_file(src, store_dir=td / "store")
            rec = store.fetch_by_id(r.taxonomy_id, td / "store")
            self.assertEqual({c["category"] for c in rec["codes"]}, {"A", "C"})

    def test_registers_real_adamast_pipeline_file_without_traces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = register_taxonomy_file(
                REAL_ADAMAST_OUTPUT,
                store_dir=td / "store",
                taxonomy_id="real-adamast-output",
            )
            rec = store.fetch_by_id(r.taxonomy_id, td / "store")
            self.assertEqual({code["id"] for code in rec["codes"]}, {"A.1", "B.1"})
            self.assertEqual(rec["domain"], "Software Engineering / Code Repair")

    def test_honors_explicit_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "t.json"
            src.write_text(json.dumps(FLAT))
            r = register_taxonomy_file(
                src, store_dir=td / "store", taxonomy_id="my-imported-tax",
            )
            self.assertEqual(r.taxonomy_id, "my-imported-tax")

    def test_duplicate_id_without_replace_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "t.json"
            src.write_text(json.dumps(FLAT))
            register_taxonomy_file(src, store_dir=td / "store",
                                    taxonomy_id="dup")
            with self.assertRaises(store.TaxonomyAlreadyExists):
                register_taxonomy_file(src, store_dir=td / "store",
                                        taxonomy_id="dup")

    def test_replace_true_allows_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "t.json"
            src.write_text(json.dumps(FLAT))
            register_taxonomy_file(src, store_dir=td / "store",
                                    taxonomy_id="dup")
            # Replace with a different domain to prove the file was overwritten.
            altered = dict(FLAT, domain="replacement-domain")
            src.write_text(json.dumps(altered))
            register_taxonomy_file(src, store_dir=td / "store",
                                    taxonomy_id="dup", replace=True)
            rec = store.fetch_by_id("dup", td / "store")
            self.assertEqual(rec["domain"], "replacement-domain")

    def test_repo_override_wins_over_file_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "t.json"
            src.write_text(json.dumps(FLAT))  # repo="demo"
            r = register_taxonomy_file(
                src, store_dir=td / "store", repo="overridden",
            )
            rec = store.fetch_by_id(r.taxonomy_id, td / "store")
            self.assertEqual(rec["repo"], "overridden")

    def test_traces_requires_adamast_model(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "t.json"
            src.write_text(json.dumps(FLAT))
            traces = td / "traces.jsonl"
            traces.write_text(json.dumps({
                "problem_id": "p1", "task": "t",
                "raw_trajectory": "r", "metadata": {},
            }))
            with self.assertRaises(ValueError):
                register_taxonomy_file(
                    src, store_dir=td / "store", traces=traces,
                    # no adamast_model
                )

    def test_traces_runs_reflection_refinement(self) -> None:
        """When traces + adamast_model + injected LLMs are provided, the
        refinement path runs. The judge stub returns no failure_points
        (so existing codes look unused on the support set) — that surfaces
        a retirement signal, so the refiner stub IS called. We return
        empty mutation proposals so the candidate is registered unchanged.
        """
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "t.json"
            src.write_text(json.dumps(FLAT))
            traces = td / "traces.jsonl"
            traces.write_text(json.dumps({
                "problem_id": "p1", "task": "t",
                "raw_trajectory": "r", "metadata": {},
            }))

            def judge_stub(user, system, *, max_tokens=8192, meter=None,
                           warnings=None):
                return {
                    "trace_summary": {"overall_judgment": "success"},
                    "events": [],
                    "failure_points": [],
                    "relations": [],
                }

            refiner_calls = {"n": 0}

            def refiner_stub(prompt, model):
                refiner_calls["n"] += 1
                # No mutations — keep the taxonomy as-is.
                return json.dumps({
                    "retire": [], "edit": [], "split": [], "add": [],
                })

            r = register_taxonomy_file(
                src, store_dir=td / "store", trace_root=td / "trace-root",
                traces=traces, adamast_model="stub-model",
                judge_call=judge_stub, refiner_call=refiner_stub,
            )
            self.assertEqual(r.trace_count, 1)
            committed = list((td / "trace-root" / r.taxonomy_id).glob("trace-*.json"))
            self.assertEqual(len(committed), 1,
                             "supporting traces must land under the given trace_root")
            self.assertTrue(r.refinement["applied"])
            self.assertEqual(refiner_calls["n"], 1,
                             "refiner runs once when there are utilization signals")
            self.assertEqual(r.refinement["retired"], [])
            self.assertEqual(r.refinement["added"], [])
            # The 2 input codes had times_mapped=0 each, so refinement
            # records them as "unused in sample".
            self.assertEqual(r.refinement["n_unused_codes_in_sample"], 2)


class RegisterTaxonomyFilesBatchTests(unittest.TestCase):
    def test_batch_register_three_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            paths = []
            for i in range(3):
                p = td / f"t{i}.json"
                p.write_text(json.dumps({**FLAT, "domain": f"domain-{i}"}))
                paths.append(p)
            results = register_taxonomy_files(paths, store_dir=td / "store")
            ok = [r for r in results if isinstance(r, RegisteredTaxonomyResult)]
            self.assertEqual(len(ok), 3)
            self.assertEqual(
                len({r.taxonomy_id for r in ok}),
                3,
                "auto-allocated ids must be unique even from identical content",
            )

    def test_batch_continues_on_error_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            good = td / "good.json"
            good.write_text(json.dumps(FLAT))
            bad = td / "bad.json"
            bad.write_text(json.dumps({"nope": "x"}))
            third = td / "third.json"
            third.write_text(json.dumps(FLAT))
            results = register_taxonomy_files(
                [good, bad, third], store_dir=td / "store",
            )
            self.assertEqual(len(results), 3)
            self.assertIsInstance(results[0], RegisteredTaxonomyResult)
            self.assertIsInstance(results[1], dict)
            self.assertIn("error", results[1])
            self.assertIsInstance(results[2], RegisteredTaxonomyResult)

    def test_batch_stop_on_error_re_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            good = td / "good.json"
            good.write_text(json.dumps(FLAT))
            bad = td / "bad.json"
            bad.write_text(json.dumps({"nope": "x"}))
            with self.assertRaises(ValueError):
                register_taxonomy_files(
                    [good, bad], store_dir=td / "store",
                    continue_on_error=False,
                )


class ExpandPathsTests(unittest.TestCase):
    def test_expands_directory_to_json_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            for n in ("a.json", "b.json", "c.json"):
                (td / n).write_text("{}")
            (td / "notes.txt").write_text("ignore me")
            paths = _expand_paths([td])
            self.assertEqual([p.name for p in paths], ["a.json", "b.json", "c.json"])

    def test_passes_through_single_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.json"
            p.write_text("{}")
            self.assertEqual(_expand_paths([p]), [p])

    def test_missing_path_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            _expand_paths(["/path/does/not/exist.json"])


if __name__ == "__main__":
    unittest.main()
