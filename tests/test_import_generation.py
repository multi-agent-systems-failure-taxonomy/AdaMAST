"""User-supplied trace generation and dormant taxonomy registration."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adamast.learning.import_generation import generate_imported_taxonomy
from adamast.core.lifecycle import end_session, start_session
from adamast.core.traces import TraceStore
from adamast.core import store


def upstream_output(code_count: int = 5) -> dict:
    return {
        "annotation_layer": {
            "category_a": [
                {
                    "code": f"A.{number}",
                    "name": f"Failure {number}",
                    "definition": f"Observable imported failure {number}.",
                }
                for number in range(1, code_count + 1)
            ],
            "category_b": [],
            "category_c": [],
        },
        "full_layer": {
            "domain_info": {
                "domain": {"name": "Imported trace domain"},
            }
        },
    }


def accepting_judge(prompt: str, _model: str) -> str:
    units = re.findall(r"### UNIT ([^\s]+)", prompt)
    return json.dumps(
        {
            "per_unit": [
                {
                    "unit_id": unit,
                    "codes_fired": [
                        {
                            "code": f"A.{number}",
                            "quote": "",
                            "evidence": "supported by imported trace",
                        }
                        for number in range(1, 6)
                    ],
                }
                for unit in units
            ]
        }
    )


class ImportedTaxonomyTests(unittest.TestCase):
    def test_accepted_import_is_stored_dormant_and_can_be_inherited(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "traces.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "problem_id": "user-trace-1",
                        "task": "Diagnose a failed workflow.",
                        "raw_trajectory": (
                            "[USER] run task\n[ASSISTANT] chose wrong input\n"
                            "[TOOL ERROR] validation failed"
                        ),
                        "metadata": {"source": "user"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store_dir = root / "taxonomies"
            trace_root = root / "learning-traces"
            result = generate_imported_taxonomy(
                source,
                adamast_model="gpt-5",
                store_dir=store_dir,
                trace_root=trace_root,
                repo="user-project",
                generator=lambda _traces: upstream_output(),
                judge_call=accepting_judge,
                verbose=False,
            )

            self.assertTrue(store.exists(result.taxonomy_id, store_dir))
            record = store.fetch_by_id(result.taxonomy_id, store_dir)
            self.assertEqual(record["repo"], "user-project")
            self.assertEqual(record["domain"], "Imported trace domain")
            self.assertEqual(result.trace_count, 1)
            self.assertEqual(len(result.active_codes), 5)
            self.assertEqual(TraceStore(result.trace_path).count(), 1)
            self.assertTrue((result.artifacts_path / "import.json").is_file())

            # Importing creates no program and activates nothing by itself.
            self.assertFalse((root / "program" / ".adamast-program.json").exists())
            session = start_session(
                result.taxonomy_id,
                trace_output=root / "program",
                store_dir=store_dir,
                trace_root=trace_root,
                dashboard=False,
            )
            self.assertEqual(session.delivery.taxonomy_id, result.taxonomy_id)
            end_session(session)

    def test_structurally_invalid_generator_output_leaves_no_taxonomy(self):
        """The Reflection Judge + refiner never rejects; the only remaining
        gate is the structural-validity check on the candidate dict. An empty
        codes list must surface as a ValueError with no leftover state."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "trace.json"
            source.write_text(
                json.dumps(
                    {
                        "problem_id": "one",
                        "task": "task",
                        "raw_trajectory": "trajectory with a concrete failure",
                        "metadata": {},
                    }
                ),
                encoding="utf-8",
            )
            store_dir = root / "taxonomies"
            trace_root = root / "traces"
            # Generator returns an AdaMAST-shaped output with ZERO codes -> the
            # candidate is structurally invalid, generate_imported_taxonomy
            # must raise ValueError before writing anything.
            empty_output = {
                "annotation_layer": {"category_a": [], "category_b": [], "category_c": []},
                "full_layer": {
                    "category_a": {}, "category_b": {}, "category_c": {},
                    "domain_info": {"domain": {"name": "test"}},
                },
            }
            with self.assertRaises(ValueError):
                generate_imported_taxonomy(
                    source,
                    adamast_model="gpt-5",
                    store_dir=store_dir,
                    trace_root=trace_root,
                    skip_judge=True,
                    generator=lambda _traces: empty_output,
                    verbose=False,
                )
            self.assertEqual(list(store_dir.glob("tax-*.json")), [])
            self.assertEqual(
                list(trace_root.glob("tax-*")) if trace_root.exists() else [],
                [],
            )

    def test_invalid_or_empty_source_is_rejected_before_generation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "bad.jsonl"
            source.write_text('{"unrecognized": true}\nnot-json\n', encoding="utf-8")
            called = []
            with self.assertRaisesRegex(ValueError, "no valid traces"):
                generate_imported_taxonomy(
                    source,
                    adamast_model="gpt-5",
                    store_dir=root / "store",
                    trace_root=root / "traces",
                    generator=lambda traces: called.append(traces),
                    verbose=False,
                )
            self.assertEqual(called, [])

    def test_skip_judge_bypasses_refinement(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = generate_imported_taxonomy(
                [
                    {
                        "problem_id": "raw-1",
                        "task": "task",
                        "raw_trajectory": "plain imported trajectory",
                        "metadata": {},
                    }
                ],
                adamast_model="gpt-5",
                store_dir=root / "store",
                trace_root=root / "traces",
                skip_judge=True,
                generator=lambda _traces: upstream_output(code_count=2),
                verbose=False,
            )
            self.assertEqual(result.active_codes, ("A.1", "A.2"))

    def test_plain_text_file_is_accepted_as_one_trace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "trajectory.txt"
            source.write_text(
                "User requested a task. Agent chose the wrong table.",
                encoding="utf-8",
            )
            result = generate_imported_taxonomy(
                source,
                adamast_model="gpt-5",
                store_dir=root / "store",
                trace_root=root / "traces",
                skip_judge=True,
                generator=lambda traces: (
                    self.assertEqual(len(traces), 1)
                    or upstream_output(code_count=2)
                ),
                verbose=False,
            )
            self.assertEqual(result.trace_count, 1)

    def test_artifact_failure_rolls_back_taxonomy_and_trace_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store_dir = root / "store"
            trace_root = root / "traces"
            with patch(
                "adamast.learning.import_generation._persist_artifacts",
                side_effect=OSError("artifact write failed"),
            ):
                with self.assertRaisesRegex(OSError, "artifact write failed"):
                    generate_imported_taxonomy(
                        [
                            {
                                "problem_id": "raw-1",
                                "task": "task",
                                "raw_trajectory": "trajectory",
                                "metadata": {},
                            }
                        ],
                        adamast_model="gpt-5",
                        store_dir=store_dir,
                        trace_root=trace_root,
                        skip_judge=True,
                        generator=lambda _traces: upstream_output(code_count=2),
                        verbose=False,
                    )

            self.assertEqual(list(store_dir.glob("tax-*.json")), [])
            self.assertEqual(
                list(trace_root.glob("tax-*")) if trace_root.exists() else [],
                [],
            )


if __name__ == "__main__":
    unittest.main()
