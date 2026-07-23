from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from adamast.core import store

from adamast.hosts.codex.learning_jobs import (
    drain_learning_notices,
    enqueue_learning_job,
    poll_learning_jobs,
    reconcile_learning_jobs,
)
from adamast.hosts.codex.native_worker import run_worker
from adamast.hosts.codex.subagent_protocol import (
    RECEIPT_CLOSE,
    RECEIPT_OPEN,
    capture_learning_receipt,
    claim_learning_job,
    complete_learning_job,
    complete_support_review,
)
from adamast.hosts.claude_code.subagent_protocol import (
    claim_learning_job as claim_claude_learning_job,
)
from adamast import GenerationTrace, ProgramWorkspace
from adamast.core.traces import TraceStore


class CodexLearningJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.program = self.root / "program"
        self.store_dir = self.root / "taxonomies"
        self.trace_root = self.root / "traces"
        self.workspace = ProgramWorkspace(self.program, repo="demo-project")
        self.launched: list[Path] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _append_pending(self, start: int, count: int) -> list[str]:
        return self.workspace.pending.append_many_with_names(
            GenerationTrace(
                problem_id=f"episode-{index}",
                task=f"Task {index}",
                raw_trajectory=f"Observed failure in completed episode {index}",
                metadata={"outcome": "hidden", "episode": index},
            )
            for index in range(start, start + count)
        )

    def _enqueue(self, kind: str = "generation") -> tuple[str, Path]:
        job_id = enqueue_learning_job(
            self.workspace,
            kind=kind,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
            task_group="default",
            conversation_id="conversation-1",
            codex_cli_path=sys.executable,
            launcher=self.launched.append,
        )
        return job_id, self.program / "learning_jobs" / job_id

    def test_job_carries_host_source_and_other_host_cannot_claim_it(self) -> None:
        with self.workspace.locked_manifest() as manifest:
            manifest["host"] = "codex"
            manifest["source"] = {
                "host": "Codex",
                "host_id": "codex",
                "project": "demo-project",
                "conversation_id": "codex-owner",
                "conversation_name": "Codex owner",
            }
        self._append_pending(1, 5)
        job_id = enqueue_learning_job(
            self.workspace,
            kind="generation",
            store_dir=self.store_dir,
            trace_root=self.trace_root,
            task_group="default",
            conversation_id="codex-owner",
        )
        job_dir = self.program / "learning_jobs" / job_id

        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        snapshot = json.loads(
            (job_dir / "snapshot.json").read_text(encoding="utf-8")
        )
        self.assertEqual(job["host"], "codex")
        self.assertEqual(job["source"]["host"], "Codex")
        self.assertEqual(snapshot["source"]["conversation_id"], "codex-owner")
        self.assertIsNone(
            claim_claude_learning_job(
                self.workspace,
                conversation_id="claude-intruder",
            )
        )
        claimed = claim_learning_job(
            self.workspace,
            conversation_id="codex-owner",
        )
        self.assertEqual(claimed["job_id"], job_id)

    @staticmethod
    def _candidate(snapshot: dict, *, suffix: str = "") -> dict:
        trace_ids = [item["problem_id"] for item in snapshot["traces"]]
        quotes = [
            {
                "trace_id": item["problem_id"],
                "quote": item["raw_trajectory"],
            }
            for item in snapshot["traces"]
        ]
        return {
            "decision": "replace",
            "repo": snapshot["repo"],
            "domain": "Small-company operations tooling",
            "summary": "Failures that recur while building integrated company tools.",
            "codes": [
                {
                    "id": f"OPS-1{suffix}",
                    "name": f"Simulation mistaken for integration{suffix}",
                    "description": "The UI appears complete while persistence is absent.",
                    "category": "C",
                    "evidence": {
                        "trace_ids": trace_ids,
                        "quotes": quotes,
                        "rationale": "Each cited episode exposed a missing durable boundary.",
                    },
                }
            ],
        }

    def _complete_worker(self, job_dir: Path, candidate: dict) -> list[str]:
        commands: list[str] = []

        def runner(command, *, prompt, job_dir, timeout_seconds):
            commands.extend(command)
            (job_dir / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            return SimpleNamespace(returncode=0)

        self.assertEqual(run_worker(job_dir, runner=runner), 0)
        return commands

    def test_generation_snapshot_is_immutable_and_queue_is_idempotent(self) -> None:
        self._append_pending(1, 5)
        job_id, job_dir = self._enqueue()
        snapshot_before = (job_dir / "snapshot.json").read_bytes()
        self._append_pending(6, 1)

        duplicate_id, duplicate_dir = self._enqueue()

        self.assertEqual(duplicate_id, job_id)
        self.assertEqual(duplicate_dir, job_dir)
        self.assertEqual((job_dir / "snapshot.json").read_bytes(), snapshot_before)
        snapshot = json.loads(snapshot_before)
        self.assertEqual(len(snapshot["traces"]), 5)
        self.assertTrue(all("outcome" not in trace["metadata"] for trace in snapshot["traces"]))
        self.assertEqual(
            [path.resolve() for path in self.launched],
            [job_dir.resolve()],
        )
        notices = drain_learning_notices(self.workspace, "conversation-1")
        self.assertEqual(len(notices), 1)
        self.assertIn("taxonomy generation triggered", notices[0])
        self.assertEqual(drain_learning_notices(self.workspace, "conversation-1"), [])

    def test_generation_snapshot_aborts_on_invalid_trace(self) -> None:
        self._append_pending(1, 4)
        broken = self.workspace.pending.root / "trace-broken.json"
        broken.write_text("{not-json", encoding="utf-8")

        with self.assertRaisesRegex(
            RuntimeError,
            "snapshot is incomplete.*trace-broken.json",
        ):
            self._enqueue()

    def test_native_host_job_claims_without_resolving_or_spawning_cli(self) -> None:
        self._append_pending(1, 5)
        with patch(
            "adamast.learning.learning_jobs.resolve_codex_cli"
        ) as resolve_cli, patch(
            "adamast.learning.learning_jobs._spawn_worker"
        ) as spawn_worker:
            job_id = enqueue_learning_job(
                self.workspace,
                kind="generation",
                store_dir=self.store_dir,
                trace_root=self.trace_root,
                task_group="default",
                conversation_id="conversation-1",
            )
        resolve_cli.assert_not_called()
        spawn_worker.assert_not_called()

        job_dir = self.program / "learning_jobs" / job_id
        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["state"], "queued")
        self.assertEqual(job["dispatch_mode"], "host_subagent")
        self.assertTrue((job_dir / "prompt.txt").is_file())
        self.assertTrue((job_dir / "output.schema.json").is_file())

        dispatch = claim_learning_job(
            self.workspace,
            conversation_id="conversation-1",
            lease_seconds=300,
        )
        self.assertEqual(dispatch["job_id"], job_id)
        self.assertIn("native Codex subagent", dispatch["directive"])
        self.assertTrue(dispatch["background_required"])
        self.assertIn("background task", dispatch["directive"])
        self.assertIn("Do not wait, join, poll, or delay", dispatch["directive"])
        self.assertIn(RECEIPT_OPEN, dispatch["task_prompt"])
        self.assertNotIn('"candidate":"<candidate JSON object>"', dispatch["task_prompt"])
        self.assertIsNone(
            claim_learning_job(
                self.workspace,
                conversation_id="conversation-1",
                lease_seconds=300,
            )
        )

    def test_conversation_branch_rejects_foreign_trace_and_job_owner(self) -> None:
        workspace = ProgramWorkspace(self.root / "owned-program", repo="demo-project")
        workspace.bind_conversation_branch(
            "codex-branch-owner",
            conversation_id="conversation-owner",
            host="codex",
        )
        workspace.pending.append_many_with_names(
            [GenerationTrace(
                problem_id="foreign-episode",
                task="Foreign task",
                raw_trajectory="Foreign conversation evidence",
                metadata={"conversation_id": "conversation-intruder"},
            )]
        )
        with self.assertRaisesRegex(ValueError, "trace belongs to conversation"):
            enqueue_learning_job(
                workspace,
                kind="generation",
                store_dir=self.store_dir,
                trace_root=self.trace_root,
                task_group="codex-branch-owner",
                conversation_id="conversation-owner",
            )

        owned = ProgramWorkspace(self.root / "job-owner-program", repo="demo-project")
        owned.bind_conversation_branch(
            "codex-branch-job-owner",
            conversation_id="conversation-owner",
            host="codex",
        )
        owned.pending.append_many_with_names(
            [GenerationTrace(
                problem_id="owned-episode",
                task="Owned task",
                raw_trajectory="Owned conversation evidence",
                metadata={"conversation_id": "conversation-owner"},
            )]
        )
        with self.assertRaisesRegex(
            RuntimeError, "conversation does not own the program branch"
        ):
            enqueue_learning_job(
                owned,
                kind="generation",
                store_dir=self.store_dir,
                trace_root=self.trace_root,
                task_group="codex-branch-job-owner",
                conversation_id="conversation-intruder",
            )

    def test_native_candidate_requires_independent_support_review(self) -> None:
        with self.workspace.locked_manifest() as manifest:
            manifest["host"] = "codex"
            manifest["source"] = {
                "host": "Codex",
                "host_id": "codex",
                "project": "demo-project",
                "conversation_id": "conversation-1",
                "conversation_name": "Generate project taxonomy",
            }
        self._append_pending(1, 5)
        job_id = enqueue_learning_job(
            self.workspace,
            kind="generation",
            store_dir=self.store_dir,
            trace_root=self.trace_root,
            task_group="default",
            conversation_id="conversation-1",
        )
        job_dir = self.program / "learning_jobs" / job_id
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        candidate = self._candidate(snapshot)

        generator = claim_learning_job(
            self.workspace,
            conversation_id="conversation-1",
        )
        complete_learning_job(
            job_dir,
            claim_token=generator["claim_token"],
            candidate=candidate,
        )
        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        staged = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(staged["state"], "support_queued")
        manifest_job = self.workspace.load()["interactive_learning"]["jobs"][job_id]
        self.assertEqual(manifest_job["state"], "support_queued")
        notices = drain_learning_notices(self.workspace, "conversation-1")
        self.assertTrue(
            any("evidence-support review is now queued" in item for item in notices)
        )
        self.assertIsNone(self.workspace.load()["taxonomy_id"])
        self.assertTrue((job_dir / "support_prompt.txt").is_file())
        reviewer = claim_learning_job(
            self.workspace,
            conversation_id="conversation-1",
        )
        self.assertIn("support-review subagent", reviewer["task_prompt"])
        self.assertIn('"review":{"supported":true', reviewer["task_prompt"])
        self.assertNotIn('"review":"<support review JSON object>"', reviewer["task_prompt"])
        complete_support_review(
            job_dir,
            claim_token=reviewer["claim_token"],
            review={
                "supported": True,
                "codes": [
                    {
                        "id": candidate["codes"][0]["id"],
                        "supported": True,
                        "reason": "The cited episodes directly describe the failure.",
                        "trace_ids": candidate["codes"][0]["evidence"]["trace_ids"],
                    }
                ],
            },
        )
        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        completed = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(completed["state"], "activated")
        stored = store.fetch_by_id(completed["taxonomy_id"], self.store_dir)
        self.assertEqual(stored["source"]["host"], "Codex")
        self.assertEqual(stored["source"]["conversation_id"], "conversation-1")
        validation = stored["codes"][0]["evidence"]["validation"]
        self.assertTrue(validation["independent_support_review"]["supported"])

    def test_capture_accepts_one_legacy_stringified_support_object(self) -> None:
        self._append_pending(1, 5)
        job_id = enqueue_learning_job(
            self.workspace,
            kind="generation",
            store_dir=self.store_dir,
            trace_root=self.trace_root,
            task_group="default",
            conversation_id="conversation-1",
        )
        job_dir = self.program / "learning_jobs" / job_id
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        candidate = self._candidate(snapshot)
        generator = claim_learning_job(
            self.workspace, conversation_id="conversation-1"
        )
        complete_learning_job(
            job_dir,
            claim_token=generator["claim_token"],
            candidate=candidate,
        )
        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )
        reviewer = claim_learning_job(
            self.workspace, conversation_id="conversation-1"
        )
        review = {
            "supported": True,
            "codes": [
                {
                    "id": candidate["codes"][0]["id"],
                    "supported": True,
                    "reason": "The cited episode directly supports this failure mode.",
                    "trace_ids": candidate["codes"][0]["evidence"]["trace_ids"],
                }
            ],
        }
        receipt = {
            "version": 1,
            "job_id": job_id,
            "claim_token": reviewer["claim_token"],
            "status": "support_review",
            "review": json.dumps(review),
        }

        captured = capture_learning_receipt(
            self.workspace,
            {"last_assistant_message": f"{RECEIPT_OPEN}{json.dumps(receipt)}{RECEIPT_CLOSE}"},
        )

        self.assertEqual(captured, job_id)
        written = json.loads(
            (job_dir / "support_receipt.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written["review"], review)

    def test_independent_support_review_can_reject_a_real_but_irrelevant_quote(self) -> None:
        self._append_pending(1, 5)
        job_id = enqueue_learning_job(
            self.workspace,
            kind="generation",
            store_dir=self.store_dir,
            trace_root=self.trace_root,
            task_group="default",
            conversation_id="conversation-1",
        )
        job_dir = self.program / "learning_jobs" / job_id
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        candidate = self._candidate(snapshot)
        candidate["codes"][0]["name"] = "Rocket fuel leak"
        generator = claim_learning_job(
            self.workspace,
            conversation_id="conversation-1",
        )
        complete_learning_job(
            job_dir,
            claim_token=generator["claim_token"],
            candidate=candidate,
        )
        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )
        reviewer = claim_learning_job(
            self.workspace,
            conversation_id="conversation-1",
        )
        complete_support_review(
            job_dir,
            claim_token=reviewer["claim_token"],
            review={
                "supported": False,
                "codes": [
                    {
                        "id": candidate["codes"][0]["id"],
                        "supported": False,
                        "reason": "The quote is real but unrelated to rocket fuel.",
                        "trace_ids": candidate["codes"][0]["evidence"]["trace_ids"],
                    }
                ],
            },
        )

        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        rejected = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(rejected["state"], "rejected")
        self.assertIn("independent support review rejected", rejected["last_error"])
        self.assertIsNone(self.workspace.load()["taxonomy_id"])

    def test_poll_repairs_a_missed_generation_trigger_idempotently(self) -> None:
        self._append_pending(1, 5)
        arguments = {
            "store_dir": self.store_dir,
            "trace_root": self.trace_root,
            "task_group": "default",
            "conversation_id": "conversation-1",
            "generation_threshold": 5,
            "k_init": 10,
            "k": 20,
            "freeze": False,
            "worker_model": None,
            "worker_timeout_seconds": 1800,
        }

        job_id = poll_learning_jobs(self.workspace, **arguments)
        duplicate = poll_learning_jobs(self.workspace, **arguments)

        self.assertIsNotNone(job_id)
        self.assertIsNone(duplicate)
        jobs = list((self.program / "learning_jobs").glob("*/job.json"))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(
            json.loads(jobs[0].read_text(encoding="utf-8"))["state"],
            "queued",
        )

    def test_poll_supersedes_a_legacy_detached_job_without_id_collision(self) -> None:
        self._append_pending(1, 5)
        legacy_id, legacy_dir = self._enqueue()
        legacy_path = legacy_dir / "job.json"
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        legacy.pop("dispatch_mode", None)
        legacy["state"] = "running"
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")

        native_id = poll_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
            task_group="default",
            conversation_id="conversation-1",
            generation_threshold=5,
            k_init=10,
            k=20,
            freeze=False,
            worker_model=None,
            worker_timeout_seconds=1800,
        )

        self.assertNotEqual(native_id, legacy_id)
        self.assertTrue(native_id.startswith("codex-native-generation-"))
        self.assertEqual(
            json.loads(legacy_path.read_text(encoding="utf-8"))["state"],
            "failed",
        )
        native_path = self.program / "learning_jobs" / native_id / "job.json"
        self.assertEqual(
            json.loads(native_path.read_text(encoding="utf-8"))["state"],
            "queued",
        )

    def test_poll_queues_refinement_after_the_active_taxonomy_threshold(self) -> None:
        taxonomy_id = "tax-poll-parent"
        store.register(
            {
                "taxonomy_id": taxonomy_id,
                "repo": "demo-project",
                "domain": "Operations",
                "summary": "Current taxonomy",
                "codes": [
                    {
                        "id": "OPS-1",
                        "name": "Current mode",
                        "description": "Current description",
                        "category": "A",
                    }
                ],
            },
            self.store_dir,
        )
        self.workspace.bind_inherited_taxonomy(taxonomy_id)
        trace_store = TraceStore(self.trace_root / taxonomy_id)
        names = trace_store.append_many_with_names(
            GenerationTrace(
                problem_id=f"refine-{index}",
                task=f"Refinement task {index}",
                raw_trajectory=f"Refinement evidence {index}",
            )
            for index in range(10)
        )
        self.workspace.add_refinement_traces(taxonomy_id, names)

        job_id = poll_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
            task_group="default",
            conversation_id="conversation-1",
            generation_threshold=5,
            k_init=10,
            k=20,
            freeze=False,
            worker_model=None,
            worker_timeout_seconds=1800,
        )

        self.assertIsNotNone(job_id)
        job_path = self.program / "learning_jobs" / job_id / "job.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        self.assertEqual(job["kind"], "refinement")
        self.assertEqual(job["state"], "queued")

    def test_native_claim_completion_reconciles_utf8_candidate(self) -> None:
        self._append_pending(1, 5)
        job_id = enqueue_learning_job(
            self.workspace,
            kind="generation",
            store_dir=self.store_dir,
            trace_root=self.trace_root,
            task_group="default",
            conversation_id="conversation-1",
        )
        job_dir = self.program / "learning_jobs" / job_id
        dispatch = claim_learning_job(
            self.workspace,
            conversation_id="conversation-1",
        )
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        candidate = self._candidate(snapshot)
        candidate["summary"] = "Échecs observés dans les opérations intégrées."

        with self.assertRaisesRegex(Exception, "claim token mismatch"):
            complete_learning_job(
                job_dir,
                claim_token="wrong-token",
                candidate=candidate,
            )
        self.assertFalse((job_dir / "receipt.json").exists())

        receipt = {
            "version": 1,
            "job_id": job_id,
            "claim_token": dispatch["claim_token"],
            "status": "candidate",
            "candidate": candidate,
        }
        captured = capture_learning_receipt(
            self.workspace,
            {
                "last_assistant_message": (
                    RECEIPT_OPEN
                    + json.dumps(receipt, ensure_ascii=False)
                    + RECEIPT_CLOSE
                )
            },
        )
        self.assertEqual(captured, job_id)
        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )
        reviewer = claim_learning_job(
            self.workspace,
            conversation_id="conversation-1",
        )
        complete_support_review(
            job_dir,
            claim_token=reviewer["claim_token"],
            review={
                "supported": True,
                "codes": [
                    {
                        "id": candidate["codes"][0]["id"],
                        "supported": True,
                        "reason": "The cited episodes directly support this code.",
                        "trace_ids": candidate["codes"][0]["evidence"]["trace_ids"],
                    }
                ],
            },
        )
        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["state"], "activated")
        learned = store.fetch_by_id(job["taxonomy_id"], self.store_dir)
        self.assertEqual(learned["summary"], candidate["summary"])

    def test_legacy_codex_learning_state_migrates_without_losing_notices(self) -> None:
        notice = {
            "id": "legacy-notice",
            "conversation_id": "conversation-1",
            "text": "Legacy learning notice",
        }
        with self.workspace.locked_manifest() as manifest:
            manifest["codex_learning"] = {
                "active_job_id": None,
                "jobs": {"legacy-job": {"state": "failed"}},
                "notices": [notice],
            }

        self.assertEqual(
            drain_learning_notices(self.workspace, "conversation-1"),
            ["Legacy learning notice"],
        )

        manifest = self.workspace.load()
        self.assertNotIn("codex_learning", manifest)
        self.assertEqual(
            manifest["interactive_learning"]["jobs"],
            {"legacy-job": {"state": "failed"}},
        )
        self.assertEqual(manifest["interactive_learning"]["notices"], [])

    def test_generation_activates_valid_receipt_and_preserves_later_trace(self) -> None:
        source_names = self._append_pending(1, 5)
        _, job_dir = self._enqueue()
        later_name = self._append_pending(6, 1)[0]
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        command = self._complete_worker(job_dir, self._candidate(snapshot))

        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        manifest = self.workspace.load()
        taxonomy_id = manifest["taxonomy_id"]
        self.assertTrue(taxonomy_id.startswith("tax-codex-"))
        self.assertTrue(store.exists(taxonomy_id, self.store_dir))
        self.assertEqual(self.workspace.pending.count(), 0)
        self.assertEqual(
            sorted(path.name for path in (self.trace_root / taxonomy_id).glob("trace-*.json")),
            sorted(source_names + [later_name]),
        )
        self.assertEqual(manifest["refinement"]["traces_since_refinement"], 1)
        self.assertEqual(
            manifest["refinement"]["trace_refs"],
            [{"taxonomy_id": taxonomy_id, "filename": later_name}],
        )
        self.assertIn("--disable", command)
        self.assertIn("hooks", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertNotIn("OPENAI_API_KEY", " ".join(command))
        completion = drain_learning_notices(self.workspace, "conversation-1")
        self.assertEqual(len(completion), 2)  # trigger was intentionally not drained
        self.assertIn("taxonomy generation finished", completion[-1])
        self.assertIn(taxonomy_id, completion[-1])
        self.assertEqual(drain_learning_notices(self.workspace, "conversation-1"), [])

    def test_invalid_evidence_is_rejected_without_activation(self) -> None:
        self._append_pending(1, 5)
        _, job_dir = self._enqueue()
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        candidate = self._candidate(snapshot)
        candidate["codes"][0]["evidence"]["trace_ids"] = ["not-in-snapshot"]
        self._complete_worker(job_dir, candidate)

        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        self.assertIsNone(self.workspace.load()["taxonomy_id"])
        self.assertEqual(self.workspace.pending.count(), 5)
        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["state"], "rejected")
        notices = drain_learning_notices(self.workspace, "conversation-1")
        self.assertIn("MAST remains active", notices[-1])

    def test_fabricated_evidence_quote_is_rejected_without_activation(self) -> None:
        self._append_pending(1, 5)
        _, job_dir = self._enqueue()
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        candidate = self._candidate(snapshot)
        candidate["codes"][0]["name"] = "Rocket fuel leak"
        candidate["codes"][0]["evidence"]["quotes"][0]["quote"] = (
            "Rocket fuel leaked from the launch vehicle"
        )
        self._complete_worker(job_dir, candidate)

        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["state"], "rejected")
        self.assertIn("absent from trace", job["last_error"])

    def test_oversized_candidate_is_rejected_without_activation(self) -> None:
        self._append_pending(1, 5)
        _, job_dir = self._enqueue()
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        candidate = self._candidate(snapshot)
        template = candidate["codes"][0]
        candidate["codes"] = [
            {
                **template,
                "id": f"OPS-{index}",
                "name": f"Failure mode {index}",
            }
            for index in range(31)
        ]
        self._complete_worker(job_dir, candidate)

        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        self.assertIsNone(self.workspace.load()["taxonomy_id"])
        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["state"], "rejected")
        self.assertIn("at most 30 codes", job["last_error"])

    def test_failed_worker_retries_same_snapshot_and_job_id(self) -> None:
        self._append_pending(1, 5)
        job_id, job_dir = self._enqueue()

        def failed_runner(*_args, **_kwargs):
            return SimpleNamespace(returncode=9)

        self.assertEqual(run_worker(job_dir, runner=failed_runner), 1)
        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )
        retried_id, retried_dir = self._enqueue()
        self.assertEqual(retried_id, job_id)
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        self._complete_worker(retried_dir, self._candidate(snapshot, suffix="-R"))
        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )
        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["state"], "activated")
        self.assertEqual(job["attempts"], 2)

    def test_dead_worker_lease_expires_without_disabling_mast(self) -> None:
        self._append_pending(1, 5)
        _, job_dir = self._enqueue()
        job_path = job_dir / "job.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["state"] = "running"
        job["worker_timeout_seconds"] = 1
        job["updated_at_unix"] = 1
        job_path.write_text(json.dumps(job), encoding="utf-8")

        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        expired = json.loads(job_path.read_text(encoding="utf-8"))
        self.assertEqual(expired["state"], "failed")
        self.assertIsNone(self.workspace.load()["taxonomy_id"])
        notices = drain_learning_notices(self.workspace, "conversation-1")
        self.assertIn("MAST remains active", notices[-1])

    def test_launch_failure_emits_trigger_and_finished_notices(self) -> None:
        self._append_pending(1, 5)

        def broken_launcher(_job_dir: Path) -> None:
            raise OSError("worker executable is unavailable")

        with self.assertRaisesRegex(OSError, "worker executable"):
            enqueue_learning_job(
                self.workspace,
                kind="generation",
                store_dir=self.store_dir,
                trace_root=self.trace_root,
                task_group="default",
                conversation_id="conversation-1",
                codex_cli_path=sys.executable,
                launcher=broken_launcher,
            )

        notices = drain_learning_notices(self.workspace, "conversation-1")
        self.assertEqual(len(notices), 2)
        self.assertIn("taxonomy generation triggered", notices[0])
        self.assertIn("taxonomy generation finished", notices[1])
        self.assertIn("MAST remains active", notices[1])
        manifest = self.workspace.load()
        self.assertIsNone(manifest["interactive_learning"]["active_job_id"])
        job_path = next((self.program / "learning_jobs").glob("*/job.json"))
        job = json.loads(job_path.read_text(encoding="utf-8"))
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["attempts"], 1)

    def test_active_episode_delays_activation(self) -> None:
        self._append_pending(1, 5)
        _, job_dir = self._enqueue()
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        self._complete_worker(job_dir, self._candidate(snapshot))
        self.workspace.register_session("still-running", "mast")

        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )
        self.assertIsNone(self.workspace.load()["taxonomy_id"])
        self.assertEqual(
            json.loads((job_dir / "job.json").read_text(encoding="utf-8"))["state"],
            "activating",
        )

        self.workspace.finish_session("still-running")
        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )
        self.assertIsNotNone(self.workspace.load()["taxonomy_id"])

    def test_manifest_write_failure_resumes_same_activation(self) -> None:
        self._append_pending(1, 5)
        _, job_dir = self._enqueue()
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        self._complete_worker(job_dir, self._candidate(snapshot))
        real_replace = __import__("os").replace
        failed = False

        def fail_manifest_once(source, destination):
            nonlocal failed
            if Path(destination).name == ".adamast-program.json" and not failed:
                failed = True
                raise OSError("injected manifest replacement failure")
            return real_replace(source, destination)

        with patch("adamast.core.program.os.replace", side_effect=fail_manifest_once):
            reconcile_learning_jobs(
                self.workspace,
                store_dir=self.store_dir,
                trace_root=self.trace_root,
            )
        self.assertIsNone(self.workspace.load()["taxonomy_id"])
        interrupted = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(interrupted["state"], "activating")

        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )
        recovered = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(recovered["state"], "activated")
        self.assertEqual(self.workspace.load()["taxonomy_id"], recovered["taxonomy_id"])

    def test_refinement_consumes_only_frozen_refs(self) -> None:
        parent_id = "tax-parent"
        store.register(
            {
                "taxonomy_id": parent_id,
                "repo": "demo-project",
                "domain": "Operations",
                "summary": "Original taxonomy",
                "codes": [
                    {
                        "id": "OPS-OLD",
                        "name": "Old mode",
                        "description": "Original description",
                        "category": "A",
                    }
                ],
            },
            self.store_dir,
        )
        self.workspace.bind_inherited_taxonomy(parent_id)
        parent_traces = TraceStore(self.trace_root / parent_id)
        frozen_names = parent_traces.append_many_with_names(
            GenerationTrace(
                problem_id=f"refine-{index}",
                task=f"Refinement {index}",
                raw_trajectory=f"New recurring failure {index}",
            )
            for index in range(1, 3)
        )
        self.workspace.add_refinement_traces(parent_id, frozen_names)
        _, job_dir = self._enqueue("refinement")
        later_name = parent_traces.append_many_with_names(
            [
                GenerationTrace(
                    problem_id="refine-later",
                    task="Later episode",
                    raw_trajectory="This arrived after the frozen review window.",
                )
            ]
        )[0]
        self.workspace.add_refinement_traces(parent_id, [later_name])
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        self._complete_worker(job_dir, self._candidate(snapshot, suffix="-NEW"))

        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        manifest = self.workspace.load()
        self.assertNotEqual(manifest["taxonomy_id"], parent_id)
        self.assertEqual(manifest["refinement"]["traces_since_refinement"], 1)
        self.assertEqual(
            manifest["refinement"]["trace_refs"],
            [{"taxonomy_id": parent_id, "filename": later_name}],
        )
        self.assertEqual(manifest["refinement"]["rounds_completed"], 1)

    def test_stale_refinement_is_rejected_before_successor_side_effects(self) -> None:
        parent_id = "tax-parent"
        store.register(
            {
                "taxonomy_id": parent_id,
                "repo": "demo-project",
                "domain": "Operations",
                "codes": [
                    {
                        "id": "OPS-OLD",
                        "name": "Old mode",
                        "description": "Original description",
                        "category": "A",
                    }
                ],
            },
            self.store_dir,
        )
        self.workspace.bind_inherited_taxonomy(parent_id)
        parent_traces = TraceStore(self.trace_root / parent_id)
        name = parent_traces.append_many_with_names(
            [
                GenerationTrace(
                    problem_id="refine-1",
                    task="Refinement",
                    raw_trajectory="Evidence for a possible successor.",
                )
            ]
        )[0]
        self.workspace.add_refinement_traces(parent_id, [name])
        _, job_dir = self._enqueue("refinement")
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        self._complete_worker(job_dir, self._candidate(snapshot, suffix="-STALE"))
        self.workspace.follow_taxonomy_successor("tax-unrelated")

        reconcile_learning_jobs(
            self.workspace,
            store_dir=self.store_dir,
            trace_root=self.trace_root,
        )

        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["state"], "rejected")
        self.assertEqual(self.workspace.load()["taxonomy_id"], "tax-unrelated")
        self.assertFalse(store.exists(job["taxonomy_id"], self.store_dir))

    def test_run_codex_feeds_prompt_as_utf8_under_locale_codec(self) -> None:
        # Same regression class as the Claude worker: the prompt pipe must
        # be UTF-8 regardless of the host locale codec.
        harness = textwrap.dedent(
            """
            import sys
            from pathlib import Path

            from adamast.hosts.codex.native_worker import _run_codex

            echo = (
                "import sys;"
                "data = sys.stdin.buffer.read().decode('utf-8');"
                "sys.stdout.buffer.write(data.encode('utf-8'))"
            )
            job_dir = Path(sys.argv[1])
            completed = _run_codex(
                [sys.executable, "-c", echo],
                prompt="taxonomy \\u2192 worker",
                job_dir=job_dir,
                timeout_seconds=60,
            )
            stderr_text = (job_dir / "stderr.log").read_text(
                encoding="utf-8", errors="replace"
            )
            assert completed.returncode == 0, stderr_text
            events = (job_dir / "events.jsonl").read_text(encoding="utf-8")
            assert "\\u2192" in events, ascii(events)
            print("ROUNDTRIP-OK")
            """
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONIOENCODING", "PYTHONUTF8"}
        }
        env["LC_ALL"] = "C"  # POSIX twin of the Windows ANSI code page
        env["LANG"] = "C"
        completed = subprocess.run(
            [sys.executable, "-X", "utf8=0", "-c", harness, str(self.root)],
            capture_output=True,
            timeout=120,
            env=env,
            cwd=Path(__file__).resolve().parent.parent,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(b"ROUNDTRIP-OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
