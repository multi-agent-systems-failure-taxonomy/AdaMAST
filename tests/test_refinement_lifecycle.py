"""Program-local refinement counters and global taxonomy lineage."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from adamast.core.lifecycle import end_session, record_trace, start_session
from adamast.core.lineage import TaxonomyLineage
from adamast.core.program import ProgramWorkspace
from adamast.learning.refinement import (
    overlap_lint,
    structural_diff,
    trigger_refinement,
)
from adamast.core.traces import GenerationTrace
from adamast.core.worker_state import REFINEMENT_WORKER_STATE, write_worker_state

ROOT = Path(__file__).resolve().parent.parent
BASE_STORE = ROOT / "tests" / "fixtures" / "taxonomies"
TRACE_FIXTURE = Path(__file__).parent / "fixtures" / "adamast_generation_trace.json"
BASE_ID = "tax-django-orm-001"


def copy_store(destination: Path) -> None:
    destination.mkdir()
    for source in BASE_STORE.glob("*.json"):
        (destination / source.name).write_bytes(source.read_bytes())


def trace(number: int) -> GenerationTrace:
    record = json.loads(TRACE_FIXTURE.read_text(encoding="utf-8"))
    record["problem_id"] = f"refine-{number}"
    return GenerationTrace.from_dict(record)


def refine_candidate(current, _traces):
    return {
        "repo": current["repo"],
        "domain": current["domain"],
        "codes": current["codes"] + [
            {
                "id": "R.NEW",
                "name": "Refined mode",
                "description": "A refinement fixture mode.",
                "category": "Refinement"
            }
        ],
    }


class RefinementDiffTests(unittest.TestCase):
    def test_structural_diff_records_code_id_mapping(self):
        current = {
            "repo": "demo",
            "domain": "d",
            "codes": [
                {"id": "A.1", "name": "Keep", "description": "old", "category": "A"},
                {"id": "A.2", "name": "Remove", "description": "old", "category": "A"},
            ],
        }
        candidate = {
            "repo": "demo",
            "domain": "d",
            "codes": [
                {"id": "A.1", "name": "Keep", "description": "edited", "category": "A"},
                {"id": "A.3", "name": "Add", "description": "new", "category": "A"},
            ],
        }

        diff = structural_diff(current, candidate)

        self.assertEqual(diff["codes_changed"], ["A.1"])
        self.assertEqual(
            diff["code_id_mapping"]["old_to_new"],
            {"A.1": "A.1", "A.2": None},
        )
        self.assertEqual(
            diff["code_id_mapping"]["new_from_old"],
            {"A.1": "A.1", "A.3": None},
        )

    def test_overlap_lint_warns_without_rejecting_candidate(self):
        candidate = {
            "repo": "demo",
            "domain": "d",
            "codes": [
                {
                    "id": "A.1",
                    "name": "Skipped verification",
                    "description": "The agent skips validation before completion.",
                    "category": "A",
                },
                {
                    "id": "A.2",
                    "name": "Skipped verification",
                    "description": "The agent skips validation before completion.",
                    "category": "A",
                },
            ],
        }

        warnings = overlap_lint(candidate)

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code_a"], "A.1")
        self.assertEqual(warnings[0]["code_b"], "A.2")


class RefinementLifecycleTests(unittest.TestCase):
    def _run_task(
        self,
        output,
        store_dir,
        trace_root,
        number,
        **kwargs,
    ):
        session = start_session(
            BASE_ID,
            trace_output=output,
            store_dir=store_dir,
            trace_root=trace_root,
            **{key: value for key, value in kwargs.items()
               if key in {
                   "k_init", "k", "refinement_stops",
                   "advanced_refinement", "adamast_model",
               }},
        )
        record_trace(session, trace(number))
        result = end_session(
            session,
            **{key: value for key, value in kwargs.items()
               if key in {
                   "refiner",
                   "refinement_approver",
                   "refinement_judge",
                   "refinement_repairer",
                   "refinement_background_launcher",
               }},
        )
        return session, result

    def test_inherited_taxonomy_uses_k_init_then_k(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)

            _, first = self._run_task(
                output, store_dir, trace_root, 1,
                k_init=2, k=3, refinement_stops=True,
                refiner=refine_candidate,
            )
            self.assertEqual(first.refinement.action, "none")

            session, second = self._run_task(
                output, store_dir, trace_root, 2,
                k_init=2, k=3, refinement_stops=True,
                refiner=refine_candidate,
            )
            self.assertEqual(second.refinement.action, "activated")
            first_refined = second.refinement.taxonomy_id
            state = session.workspace.refinement_state()
            self.assertEqual(state["rounds_completed"], 1)
            self.assertEqual(state["traces_since_refinement"], 0)

            for number in (3, 4):
                _, result = self._run_task(
                    output, store_dir, trace_root, number,
                    k_init=2, k=3, refinement_stops=True,
                    refiner=refine_candidate,
                )
                self.assertEqual(result.refinement.action, "none")
            session, fifth = self._run_task(
                output, store_dir, trace_root, 5,
                k_init=2, k=3, refinement_stops=True,
                refiner=refine_candidate,
            )
            self.assertEqual(fifth.refinement.action, "activated")
            self.assertNotEqual(fifth.refinement.taxonomy_id, first_refined)
            self.assertEqual(
                session.workspace.refinement_state()["rounds_completed"],
                2,
            )

    def test_other_program_follows_successor_and_preserves_counter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            program_a, program_b = root / "a", root / "b"
            store_dir, trace_root = root / "tax", root / "traces"
            copy_store(store_dir)

            b_session, b_first = self._run_task(
                program_b, store_dir, trace_root, 1,
                k_init=2, k=20,
            )
            self.assertEqual(b_first.refinement.action, "none")
            self.assertEqual(
                b_session.workspace.refinement_state()["traces_since_refinement"],
                1,
            )

            for number in (2, 3):
                a_session, a_result = self._run_task(
                    program_a, store_dir, trace_root, number,
                    k_init=2, k=20, refinement_stops=True,
                    refiner=refine_candidate,
                )
            self.assertEqual(a_result.refinement.action, "activated")
            successor = a_result.refinement.taxonomy_id
            self.assertEqual(
                TaxonomyLineage(store_dir).resolve_latest(BASE_ID),
                successor,
            )

            b_next = start_session(
                BASE_ID,
                trace_output=program_b,
                store_dir=store_dir,
                trace_root=trace_root,
                k_init=2,
                k=20,
                refinement_stops=True,
            )
            self.assertEqual(b_next.delivery.taxonomy_id, successor)
            self.assertEqual(
                b_next.workspace.refinement_state()["traces_since_refinement"],
                1,
            )
            record_trace(b_next, trace(4))
            b_second = end_session(b_next, refiner=refine_candidate)
            self.assertEqual(b_second.refinement.action, "activated")
            self.assertEqual(
                b_next.workspace.refinement_state()["rounds_completed"],
                1,
            )

    def test_conversation_branches_can_refine_one_parent_independently(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store_dir, trace_root = root / "tax", root / "traces"
            copy_store(store_dir)
            children = []
            branches = []
            for number, conversation_id in enumerate(("conversation-a", "conversation-b"), 1):
                workspace = ProgramWorkspace(root / conversation_id)
                workspace.bind_conversation_branch(
                    f"branch-{conversation_id}",
                    conversation_id=conversation_id,
                    host="codex",
                )
                session = start_session(
                    BASE_ID,
                    trace_output=workspace.root,
                    store_dir=store_dir,
                    trace_root=trace_root,
                    k_init=1,
                    refinement_stops=True,
                )
                record_trace(session, trace(number))
                result = end_session(session, refiner=refine_candidate)
                self.assertEqual(result.refinement.action, "activated")
                children.append(result.refinement.taxonomy_id)
                branches.append(session.workspace)

            self.assertNotEqual(children[0], children[1])
            self.assertEqual(
                set(TaxonomyLineage(store_dir).children(BASE_ID)),
                set(children),
            )
            with self.assertRaisesRegex(ValueError, "branch-relative"):
                TaxonomyLineage(store_dir).resolve_latest(BASE_ID)
            self.assertEqual(branches[0].load()["taxonomy_id"], children[0])
            self.assertEqual(branches[1].load()["taxonomy_id"], children[1])
            self.assertTrue(
                (trace_root / "branches" / "branch-conversation-a" / BASE_ID).is_dir()
            )
            self.assertTrue(
                (trace_root / "branches" / "branch-conversation-b" / BASE_ID).is_dir()
            )

    def test_rejected_refinement_preserves_counter_and_taxonomy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)

            session, result = self._run_task(
                output, store_dir, trace_root, 1,
                k_init=1, k=20, refinement_stops=True,
                refiner=refine_candidate,
                refinement_approver=lambda _candidate: False,
            )
            self.assertEqual(result.refinement.action, "rejected")
            self.assertEqual(session.workspace.load()["taxonomy_id"], BASE_ID)
            state = session.workspace.refinement_state()
            self.assertEqual(state["traces_since_refinement"], 1)
            self.assertEqual(state["rounds_completed"], 0)

    def test_failed_refinement_preserves_counter_and_taxonomy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)

            def broken(_current, _traces):
                raise RuntimeError("refiner unavailable")

            session, result = self._run_task(
                output, store_dir, trace_root, 1,
                k_init=1, k=20, refinement_stops=True,
                refiner=broken,
            )
            self.assertEqual(result.refinement.action, "failed")
            self.assertEqual(session.workspace.load()["taxonomy_id"], BASE_ID)
            state = session.workspace.refinement_state()
            self.assertEqual(state["traces_since_refinement"], 1)
            self.assertEqual(state["rounds_completed"], 0)

    def test_failed_refinement_rearms_next_round(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)

            def broken(_current, _traces):
                raise RuntimeError("invalid JSON")

            session, result = self._run_task(
                output, store_dir, trace_root, 1,
                k_init=1, k=20, refinement_stops=True,
                refiner=broken,
            )
            self.assertEqual(result.refinement.action, "failed")

            retried = trigger_refinement(
                session.workspace,
                store_dir=store_dir,
                trace_root=trace_root,
                k_init=1,
                k=20,
                refinement_stops=True,
                refiner=refine_candidate,
            )
            self.assertEqual(retried.action, "activated")
            self.assertEqual(
                session.workspace.refinement_state()["traces_since_refinement"],
                0,
            )

    def test_freeze_mode_integrates_inherited_trace_without_refinement_counter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)

            session = start_session(
                BASE_ID,
                trace_output=output,
                store_dir=store_dir,
                trace_root=trace_root,
                k_init=1,
                refinement_stops=True,
                freeze=True,
            )
            record_trace(session, trace(1))
            result = end_session(
                session,
                refiner=lambda _current, _traces: self.fail(
                    "refinement should be frozen"
                ),
            )

            self.assertEqual(result.refinement.action, "frozen")
            self.assertEqual(result.integrated_traces, 1)
            self.assertEqual(session.workspace.pending.count(), 0)
            self.assertEqual(len(list((trace_root / BASE_ID).glob("*.json"))), 1)
            state = session.workspace.refinement_state()
            self.assertEqual(state["traces_since_refinement"], 0)
            self.assertEqual(state["trace_refs"], [])
            self.assertEqual(session.workspace.load()["taxonomy_id"], BASE_ID)

    def test_nonblocking_refinement_starts_and_keeps_current_taxonomy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            launched = []
            session, result = self._run_task(
                output, store_dir, trace_root, 1,
                k_init=1, k=20,
                refinement_background_launcher=lambda: launched.append(True),
            )
            self.assertEqual(result.refinement.action, "started")
            self.assertEqual(launched, [True])
            self.assertEqual(session.workspace.load()["taxonomy_id"], BASE_ID)
            self.assertEqual(
                session.workspace.refinement_state()["traces_since_refinement"],
                1,
            )

    def test_stale_background_refinement_worker_can_be_retried(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            workspace = ProgramWorkspace(output)
            workspace.bind_inherited_taxonomy(BASE_ID)
            with workspace.locked_manifest() as manifest:
                manifest["refinement"].update(
                    {
                        "traces_since_refinement": 1,
                        "state": "running",
                        "last_error": None,
                        "worker_kind": "background",
                        "worker_started_unix": time.time() - 1_000,
                    }
                )
            write_worker_state(
                output / REFINEMENT_WORKER_STATE,
                "refinement",
                pid=123,
                now=time.time() - 1_000,
            )
            launched = []

            result = trigger_refinement(
                workspace,
                store_dir=store_dir,
                trace_root=trace_root,
                k_init=1,
                background_launcher=lambda: launched.append(True),
            )

            self.assertEqual(result.action, "started")
            self.assertEqual(launched, [True])
            refinement = workspace.refinement_state()
            self.assertEqual(refinement["state"], "running")
            self.assertIsNone(refinement["last_error"])

    def test_basic_refinement_persists_structural_diff(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            _, result = self._run_task(
                output, store_dir, trace_root, 1,
                k_init=1, k=20, refinement_stops=True,
                refiner=refine_candidate,
            )
            artifact = json.loads(
                (store_dir / "_state" / "refinements"
                 / f"{result.refinement.taxonomy_id}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(artifact["from_taxonomy_id"], BASE_ID)
            self.assertEqual(artifact["to_taxonomy_id"], result.refinement.taxonomy_id)
            self.assertEqual(artifact["diff"]["codes_added"], ["R.NEW"])
            self.assertFalse(artifact["advanced_refinement"])
            self.assertFalse(artifact["repaired"])
            self.assertEqual(artifact["overlap_warnings"], [])
            usage = ProgramWorkspace(output).load()["usage"]
            self.assertEqual(usage["totals"]["calls"], 1)
            self.assertEqual(usage["events"][0]["stage"], "taxonomy_refinement")

    def test_advanced_refinement_without_issues_accepts_without_repair(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            calls = {"judge": 0, "repair": 0}

            def judge(_current, _candidate, _diff, _traces, model):
                calls["judge"] += 1
                self.assertEqual(model, "claude-sonnet-4-6")
                return []

            def repair(*_args):
                calls["repair"] += 1
                raise AssertionError("repair must not run")

            _, result = self._run_task(
                output, store_dir, trace_root, 1,
                k_init=1, k=20, refinement_stops=True,
                advanced_refinement=True,
                adamast_model="claude-sonnet-4-6",
                refiner=refine_candidate,
                refinement_judge=judge,
                refinement_repairer=repair,
            )
            self.assertEqual(result.refinement.action, "activated")
            self.assertEqual(calls, {"judge": 1, "repair": 0})

    def test_advanced_refinement_repairs_once_and_does_not_rejudge(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)
            calls = {"judge": 0, "repair": 0}

            def judge(_current, _candidate, _diff, _traces, _model):
                calls["judge"] += 1
                return [{"code": "R.NEW", "issue": "description is vague"}]

            def repair(current, candidate, diff, _traces, issues, model):
                calls["repair"] += 1
                self.assertEqual(diff["codes_added"], ["R.NEW"])
                self.assertEqual(len(issues), 1)
                self.assertEqual(model, "claude-sonnet-4-6")
                repaired = dict(candidate)
                repaired["codes"] = [
                    ({**code, "description": "A precise repaired description."}
                     if code.get("id") == "R.NEW" else code)
                    for code in candidate["codes"]
                ]
                return repaired

            _, result = self._run_task(
                output, store_dir, trace_root, 1,
                k_init=1, k=20, refinement_stops=True,
                advanced_refinement=True,
                adamast_model="claude-sonnet-4-6",
                refiner=refine_candidate,
                refinement_judge=judge,
                refinement_repairer=repair,
            )
            self.assertEqual(result.refinement.action, "activated")
            self.assertEqual(calls, {"judge": 1, "repair": 1})
            record = json.loads(
                (store_dir / f"{result.refinement.taxonomy_id}.json").read_text(
                    encoding="utf-8"
                )
            )
            repaired = next(
                code for code in record["codes"] if code.get("id") == "R.NEW"
            )
            self.assertEqual(repaired["description"], "A precise repaired description.")
            artifact = json.loads(
                (store_dir / "_state" / "refinements"
                 / f"{result.refinement.taxonomy_id}.json").read_text(encoding="utf-8")
            )
            self.assertTrue(artifact["advanced_refinement"])
            self.assertTrue(artifact["repaired"])
            self.assertEqual(len(artifact["judge_issues"]), 1)

    def test_failed_advanced_repair_preserves_current_taxonomy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output, store_dir, trace_root = root / "program", root / "tax", root / "traces"
            copy_store(store_dir)

            def broken_repair(*_args):
                raise RuntimeError("repair unavailable")

            session, result = self._run_task(
                output, store_dir, trace_root, 1,
                k_init=1, k=20, refinement_stops=True,
                advanced_refinement=True,
                adamast_model="claude-sonnet-4-6",
                refiner=refine_candidate,
                refinement_judge=lambda *_args: [{"issue": "repair this"}],
                refinement_repairer=broken_repair,
            )
            self.assertEqual(result.refinement.action, "failed")
            self.assertEqual(session.workspace.load()["taxonomy_id"], BASE_ID)
            self.assertEqual(
                session.workspace.refinement_state()["traces_since_refinement"], 1
            )


if __name__ == "__main__":
    unittest.main()
