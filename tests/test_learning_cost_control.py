"""D2: completion-chaining parity + lifetime cap on auto-launched cycles."""

import tempfile
import unittest
from pathlib import Path

from adamast.core.program import ProgramWorkspace
from adamast.core.traces import GenerationTrace
from adamast.learning.learning_jobs import (
    MAX_AUTO_LEARNING_CYCLES,
    _loaded_learning_state,
    _manifest_learning_state,
    poll_learning_jobs,
)


class CompletionChainingParityTests(unittest.TestCase):
    def test_both_hosts_dispatch_learning_on_subagent_completion(self):
        from adamast.hosts.claude_code import dispatcher as claude_dispatcher
        from adamast.hosts.codex import dispatcher as codex_dispatcher

        # D2: the next learning phase is dispatched when a taxonomy-worker
        # subagent finishes, not only on the following user prompt.
        self.assertIn(
            "SubagentStop", codex_dispatcher._LEARNING_DISPATCH_EVENTS
        )
        self.assertIn(
            "SubagentStop", claude_dispatcher._LEARNING_DISPATCH_EVENTS
        )
        # Both hosts must dispatch on the same lifecycle events.
        self.assertEqual(
            codex_dispatcher._LEARNING_DISPATCH_EVENTS,
            claude_dispatcher._LEARNING_DISPATCH_EVENTS,
        )


def _workspace(root: Path, *, pending: int) -> ProgramWorkspace:
    workspace = ProgramWorkspace(root / "program", repo="demo")
    workspace.pending.append_many_with_names(
        GenerationTrace(
            problem_id=f"ep-{index}",
            task=f"Task {index}",
            raw_trajectory=f"Evidence {index}",
        )
        for index in range(1, pending + 1)
    )
    return workspace


def _poll(workspace: ProgramWorkspace, calls: list, root: Path):
    return poll_learning_jobs(
        workspace,
        enqueue_job=lambda kind: (calls.append(kind), "job-x")[1],
        store_dir=root / "tax",
        trace_root=root / "traces",
        generation_threshold=5,
        k_init=10,
        k=20,
        freeze=False,
    )


class AutoLearningCycleCapTests(unittest.TestCase):
    def test_generation_launches_and_bumps_the_cycle_counter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = _workspace(root, pending=5)
            calls: list[str] = []
            result = _poll(workspace, calls, root)
            self.assertEqual(calls, ["generation"])
            self.assertIsNotNone(result)
            learning = _loaded_learning_state(workspace.load())
            self.assertEqual(int(learning.get("auto_cycles", 0)), 1)

    def test_cap_parks_further_auto_launches(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = _workspace(root, pending=5)
            with workspace.locked_manifest() as manifest:
                _manifest_learning_state(manifest)["auto_cycles"] = (
                    MAX_AUTO_LEARNING_CYCLES
                )
            calls: list[str] = []
            result = _poll(workspace, calls, root)
            self.assertIsNone(result)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
