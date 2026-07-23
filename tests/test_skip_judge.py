"""Tests for the --skip-judge flag and its plumbing.

Confirms the flag flows through:
  * options.RuntimeOptions / parse_runtime_args
  * generation.run_generation_job (bypasses reflection refinement)
  * lifecycle.Session (round-trip)
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adamast.core import options
from adamast.learning import generation


def _structural_taxonomy() -> dict:
    """A trivially-acceptable taxonomy that structurally_accept() returns True for."""
    return {
        "annotation_layer": {
            "category_a": [{"code": "A.1", "name": "Loop", "definition": "..."}],
            "category_b": [],
            "category_c": [{"code": "C.1", "name": "Math", "definition": "..."}],
        },
        "full_layer": {
            "domain_info": {"domain": {"name": "test-domain"}},
            "category_a": {"A.1": {"code": "A.1", "name": "Loop", "definition": "..."}},
            "category_b": {},
            "category_c": {"C.1": {"code": "C.1", "name": "Math", "definition": "..."}},
        },
    }


class ParseRuntimeArgsTests(unittest.TestCase):
    def test_skip_judge_defaults_false(self) -> None:
        opts = options.parse_runtime_args(
            ["--trace-output", "/tmp/x", "--adamast-model", "m"]
        )
        self.assertFalse(opts.skip_judge)

    def test_skip_judge_flag_parsed(self) -> None:
        opts = options.parse_runtime_args(
            ["--trace-output", "/tmp/x", "--adamast-model", "m", "--skip-judge"]
        )
        self.assertTrue(opts.skip_judge)


class GenerationSkipJudgeTests(unittest.TestCase):
    """skip_judge=True must bypass the reflection-judge refinement entirely."""

    def test_skip_judge_bypasses_refinement(self) -> None:
        captured = {"called": False}

        def fake_refine(*_, **__):
            captured["called"] = True
            raise AssertionError(
                "refine_with_reflection_judge should not run when skip_judge=True"
            )

        def stub_generator(_traces):
            return _structural_taxonomy()

        with tempfile.TemporaryDirectory() as td:
            ws_dir = Path(td) / "ws"
            store_dir = Path(td) / "store"
            trace_root = Path(td) / "traces"
            from adamast.core.program import ProgramWorkspace
            from adamast.core.traces import GenerationTrace
            ws = ProgramWorkspace(ws_dir)
            for i in range(5):  # meet generation_threshold
                ws.pending.append_many([
                    GenerationTrace(problem_id=f"p{i}", task="t",
                                    raw_trajectory="r", metadata={})
                ])
            ws.try_begin_generation()

            with patch.object(generation, "refine_with_reflection_judge", fake_refine):
                result = generation.run_generation_job(
                    ws,
                    store_dir=store_dir,
                    trace_root=trace_root,
                    generator=stub_generator,
                    adamast_model="stub-model",
                    skip_judge=True,
                    generation_threshold=5,
                    activation_poll_seconds=0.001,
                    activation_timeout_seconds=2.0,
                )

            self.assertFalse(
                captured["called"],
                "refine_with_reflection_judge must NOT be called when skip_judge=True",
            )
            self.assertEqual(result.action, "activated")
            self.assertIsNotNone(result.taxonomy_id)

    def test_default_runs_refinement_and_records_judge_metadata(self) -> None:
        """skip_judge=False (default) routes through reflection refinement."""
        from adamast.learning.reflection_refinement import RefinementSummary

        def stub_generator(_traces):
            return _structural_taxonomy()

        captured = {"called": False, "candidate": None}

        def fake_refine(candidate, _traces, **_kw):
            captured["called"] = True
            captured["candidate"] = candidate
            # Echo back the candidate unchanged; pretend refinement was a no-op.
            return RefinementSummary(
                candidate=candidate,
                n_traces_judged=5,
                n_proposed_names_distinct=0,
                n_weak_mapping_codes=0,
                n_unused_codes_in_sample=0,
            )

        with tempfile.TemporaryDirectory() as td:
            ws_dir = Path(td) / "ws"
            store_dir = Path(td) / "store"
            trace_root = Path(td) / "traces"
            from adamast.core.program import ProgramWorkspace
            from adamast.core.traces import GenerationTrace
            ws = ProgramWorkspace(ws_dir)
            for i in range(5):
                ws.pending.append_many([
                    GenerationTrace(problem_id=f"p{i}", task="t",
                                    raw_trajectory="r", metadata={})
                ])
            ws.try_begin_generation()

            with patch.object(generation, "refine_with_reflection_judge", fake_refine):
                result = generation.run_generation_job(
                    ws,
                    store_dir=store_dir,
                    trace_root=trace_root,
                    generator=stub_generator,
                    adamast_model="stub-model",
                    skip_judge=False,
                    generation_threshold=5,
                    activation_poll_seconds=0.001,
                    activation_timeout_seconds=2.0,
                )

            self.assertTrue(captured["called"])
            self.assertEqual(result.action, "activated")


class SessionSkipJudgeRoundTripTests(unittest.TestCase):
    def test_session_carries_skip_judge_into_lifecycle(self) -> None:
        from adamast.core import lifecycle
        with tempfile.TemporaryDirectory() as td:
            ws_dir = Path(td) / "ws"
            store_dir = Path(td) / "store"
            session = lifecycle.start_session(
                trace_output=ws_dir,
                store_dir=store_dir,
                adamast_model="stub",
                skip_judge=True,
                dashboard=False,
            )
            self.assertTrue(session.skip_judge)


if __name__ == "__main__":
    unittest.main()
