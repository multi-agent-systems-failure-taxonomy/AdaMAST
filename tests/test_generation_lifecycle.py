"""MAST warm-up generation lifecycle tests."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from adamast.learning.generation import (
    candidate_from_adamast,
    run_generation_job,
    structurally_accept,
    trigger_generation,
)
from adamast.core.lifecycle import end_session, record_trace, start_session
from adamast.core.program import ProgramWorkspace
from adamast.core.traces import GenerationTrace, TraceStore
from adamast.core.worker_state import GENERATION_WORKER_STATE, write_worker_state
from adamast.core import resolver, store

ROOT = Path(__file__).resolve().parent.parent
BASE_STORE = ROOT / "tests" / "fixtures" / "taxonomies"
TRACE_FIXTURE = Path(__file__).parent / "fixtures" / "adamast_generation_trace.json"
ADAMAST_OUTPUT = Path(__file__).parent / "fixtures" / "real_adamast_generation_output.json"


def trace(number: int) -> GenerationTrace:
    record = json.loads(TRACE_FIXTURE.read_text(encoding="utf-8"))
    record["problem_id"] = f"warmup-{number}"
    return GenerationTrace.from_dict(record)


def real_generation_output():
    return json.loads(ADAMAST_OUTPUT.read_text(encoding="utf-8"))


def copy_store(destination: Path) -> None:
    destination.mkdir()
    for source in BASE_STORE.glob("*.json"):
        (destination / source.name).write_bytes(source.read_bytes())


class GenerationLifecycleTests(unittest.TestCase):
    def _finish_four(self, output, store_dir, trace_root):
        for number in range(4):
            session = start_session(
                resolver.ABSENT,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
            )
            record_trace(session, trace(number))
            result = end_session(session)
            self.assertEqual(result.generation.action, "none")

    def test_blocking_generation_activates_only_after_acceptance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            self._finish_four(output, store_dir, trace_root)
            seen_candidate = {}

            def approve(candidate):
                seen_candidate.update(candidate)
                self.assertNotIn("taxonomy_id", candidate)
                return True

            fifth = start_session(
                resolver.ABSENT,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
                generation_stops=True,
                adamast_model="claude-sonnet-4-6",
                skip_judge=True,
            )
            record_trace(fifth, trace(5))
            result = end_session(
                fifth,
                generator=lambda _traces: real_generation_output(),
                approver=approve,
            )

            self.assertEqual(result.generation.action, "activated")
            taxonomy_id = result.generation.taxonomy_id
            self.assertTrue(taxonomy_id)
            self.assertEqual(fifth.workspace.pending.count(), 0)
            self.assertEqual(TraceStore(trace_root / taxonomy_id).count(), 5)
            self.assertTrue(store.exists(taxonomy_id, store_dir))
            self.assertEqual(fifth.workspace.load()["taxonomy_id"], taxonomy_id)
            usage = fifth.workspace.load()["usage"]
            self.assertEqual(usage["totals"]["calls"], 1)
            self.assertEqual(
                usage["events"][0]["stage"],
                "taxonomy_generation",
            )
            self.assertFalse(usage["events"][0]["usage_available"])
            self.assertEqual(
                fifth.workspace.refinement_state()["traces_since_refinement"],
                0,
            )
            self.assertEqual(seen_candidate["repo"], fifth.workspace.repo)
            self.assertEqual(
                seen_candidate["domain"],
                "Software Engineering / Code Repair",
            )

            first_new_task = start_session(
                resolver.ABSENT,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
                k_init=2,
                k=20,
                refinement_stops=True,
            )
            self.assertEqual(first_new_task.delivery.taxonomy_id, taxonomy_id)
            record_trace(first_new_task, trace(6))
            first_new_result = end_session(
                first_new_task,
                refiner=lambda current, _traces: {
                    "repo": current["repo"],
                    "domain": current["domain"],
                    "codes": current["codes"],
                },
            )
            self.assertEqual(first_new_result.refinement.action, "none")
            self.assertEqual(
                first_new_task.workspace.refinement_state()[
                    "traces_since_refinement"
                ],
                1,
            )

    def test_rejection_preserves_pending_and_creates_no_taxonomy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            self._finish_four(output, store_dir, trace_root)
            before = {path.name for path in store_dir.glob("*.json")}

            fifth = start_session(
                resolver.ABSENT,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
                generation_stops=True,
                adamast_model="claude-sonnet-4-6",
                skip_judge=True,
            )
            record_trace(fifth, trace(5))
            result = end_session(
                fifth,
                generator=lambda _traces: real_generation_output(),
                approver=lambda _candidate: False,
            )

            self.assertEqual(result.generation.action, "rejected")
            self.assertEqual(fifth.workspace.pending.count(), 5)
            self.assertIsNone(fifth.workspace.load()["taxonomy_id"])
            self.assertEqual(
                {path.name for path in store_dir.glob("*.json")},
                before,
            )
            self.assertFalse(trace_root.exists())

    def test_generation_failure_preserves_pending(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            self._finish_four(output, store_dir, trace_root)

            fifth = start_session(
                resolver.ABSENT,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
                generation_stops=True,
                adamast_model="claude-sonnet-4-6",
                skip_judge=True,
            )
            record_trace(fifth, trace(5))

            def broken(_traces):
                raise RuntimeError("generator unavailable")

            result = end_session(fifth, generator=broken)
            self.assertEqual(result.generation.action, "failed")
            self.assertEqual(fifth.workspace.pending.count(), 5)
            self.assertIsNone(fifth.workspace.load()["taxonomy_id"])

    def test_freeze_mode_records_mast_trace_without_generation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            self._finish_four(output, store_dir, trace_root)

            fifth = start_session(
                resolver.ABSENT,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
                generation_stops=True,
                freeze=True,
            )
            record_trace(fifth, trace(5))
            result = end_session(
                fifth,
                generator=lambda _traces: self.fail("generation should be frozen"),
            )

            self.assertEqual(result.generation.action, "frozen")
            self.assertEqual(fifth.workspace.pending.count(), 5)
            self.assertIsNone(fifth.workspace.load()["taxonomy_id"])
            self.assertFalse(trace_root.exists())

    def test_session_end_can_export_durable_evidence_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            export_dir = root / "evidence-export"
            copy_store(store_dir)

            session = start_session(
                resolver.ABSENT,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
                generation_threshold=2,
                evidence_export=export_dir,
            )
            record_trace(session, trace(1))
            result = end_session(session)

            self.assertIsNone(result.evidence_export_error)
            self.assertTrue(result.evidence_export_path)
            payload = json.loads(
                result.evidence_export_path.read_text(encoding="utf-8")
            )
            self.assertEqual(payload["program_id"], session.program_id)
            self.assertEqual(payload["trace_output"], str(output.resolve()))
            self.assertIn("manifest", payload)
            self.assertIn("runtime_evidence", payload)
            self.assertEqual(result.evidence_export_path.parent, export_dir.resolve())

    def test_nonblocking_generation_keeps_mast_until_job_activates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            self._finish_four(output, store_dir, trace_root)
            launched = []

            fifth = start_session(
                resolver.ABSENT,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
                adamast_model="claude-sonnet-4-6",
                skip_judge=True,
            )
            record_trace(fifth, trace(5))
            result = end_session(
                fifth,
                background_launcher=lambda: launched.append(True),
            )
            self.assertEqual(result.generation.action, "started")
            self.assertEqual(launched, [True])
            self.assertIsNone(fifth.workspace.load()["taxonomy_id"])

            next_task = start_session(
                resolver.ABSENT,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
            )
            self.assertEqual(next_task.delivery.taxonomy_id, "mast")

            outcome = {}

            def worker():
                outcome["result"] = run_generation_job(
                    fifth.workspace,
                    store_dir=store_dir,
                    trace_root=trace_root,
                    generator=lambda _traces: real_generation_output(),
                    adamast_model="claude-sonnet-4-6",
                    skip_judge=True,
                    activation_poll_seconds=0.01,
                    # Generous ceilings: a loaded CI runner can stall the
                    # main thread past a tight window, and an activation
                    # timeout here reads as a spurious "failed" job.
                    activation_timeout_seconds=30,
                )

            thread = threading.Thread(target=worker)
            thread.start()
            time.sleep(0.05)
            self.assertIsNone(fifth.workspace.load()["taxonomy_id"])
            end_session(next_task)
            thread.join(30)
            self.assertFalse(thread.is_alive())
            self.assertEqual(outcome["result"].action, "activated")

            later = start_session(
                resolver.ABSENT,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
            )
            self.assertEqual(
                later.delivery.taxonomy_id,
                outcome["result"].taxonomy_id,
            )

    def test_configured_threshold_controls_first_generation(self):
        # Regression: the initial manifest hardcoded retry_after_count=5, so
        # generation_threshold had no effect on the first generation in any
        # integration (1 never fired early; 10 fired at 5 anyway).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            workspace = ProgramWorkspace(output)
            workspace.pending.append_many([trace(1)])
            launched = []

            result = trigger_generation(
                workspace,
                store_dir=store_dir,
                trace_root=trace_root,
                threshold=1,
                background_launcher=lambda: launched.append(True),
            )

            self.assertEqual(result.action, "started")
            self.assertEqual(launched, [True])

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            workspace = ProgramWorkspace(output)
            workspace.pending.append_many(trace(number) for number in range(5))

            result = trigger_generation(
                workspace,
                store_dir=store_dir,
                trace_root=trace_root,
                threshold=6,
                background_launcher=lambda: self.fail("must not launch"),
            )

            self.assertEqual(result.action, "none")
            self.assertIn("5/6", result.reason)

    def test_stale_background_generation_worker_can_be_retried(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            workspace = ProgramWorkspace(output)
            workspace.pending.append_many(trace(number) for number in range(5))
            with workspace.locked_manifest() as manifest:
                manifest["generation"] = {
                    "state": "running",
                    "last_error": None,
                    "worker_kind": "background",
                    "worker_started_unix": time.time() - 1_000,
                }
            write_worker_state(
                output / GENERATION_WORKER_STATE,
                "generation",
                pid=123,
                now=time.time() - 1_000,
            )
            launched = []

            result = trigger_generation(
                workspace,
                store_dir=store_dir,
                trace_root=trace_root,
                threshold=5,
                background_launcher=lambda: launched.append(True),
            )

            self.assertEqual(result.action, "started")
            self.assertEqual(launched, [True])
            generation = workspace.load()["generation"]
            self.assertEqual(generation["state"], "running")
            self.assertIsNone(generation["last_error"])

    def test_live_background_generation_worker_is_not_retried(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            workspace = ProgramWorkspace(output)
            workspace.pending.append_many(trace(number) for number in range(5))
            with workspace.locked_manifest() as manifest:
                manifest["generation"] = {
                    "state": "running",
                    "last_error": None,
                    "worker_kind": "background",
                    "worker_started_unix": time.time(),
                }
            write_worker_state(
                output / GENERATION_WORKER_STATE,
                "generation",
                pid=123,
            )
            launched = []

            result = trigger_generation(
                workspace,
                store_dir=store_dir,
                trace_root=trace_root,
                threshold=5,
                background_launcher=lambda: launched.append(True),
            )

            self.assertEqual(result.action, "none")
            self.assertEqual(result.reason, "generation already running or unnecessary")
            self.assertEqual(launched, [])


class AdaMASTCandidateConversionTests(unittest.TestCase):
    def test_keeps_step_one_discovered_domain(self):
        candidate = candidate_from_adamast(real_generation_output())
        self.assertEqual(
            candidate["domain"],
            "Software Engineering / Code Repair",
        )
        self.assertTrue(structurally_accept(candidate))

    def test_missing_domain_metadata_remains_valid_display_empty_string(self):
        raw = real_generation_output()
        raw.pop("full_layer")
        candidate = candidate_from_adamast(raw)
        self.assertEqual(candidate["domain"], "")
        self.assertTrue(structurally_accept(candidate))

    def test_codes_are_canonical_with_short_category(self):
        candidate = candidate_from_adamast(real_generation_output())
        self.assertTrue(candidate["codes"])
        for code in candidate["codes"]:
            # category is the SHORT label, never the verbose definition sentence
            self.assertIn(code["category"], {"A", "B", "C"})
            self.assertEqual(
                {"id", "name", "description", "category"} - set(code), set()
            )


if __name__ == "__main__":
    unittest.main()
