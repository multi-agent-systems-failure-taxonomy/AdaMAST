"""Line-readable worker prompts and the consecutive-failure requeue guard."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from adamast.hosts.codex.learning_jobs import (
    enqueue_learning_job,
    poll_learning_jobs,
)
from adamast.learning.learning_jobs import _fail_job
from adamast.learning.worker_contract import (
    CHUNKED_TEXT_NOTE,
    READABLE_CHUNK_CHARS,
    build_prompt,
    build_support_review_prompt,
    render_readable_json,
)
from adamast import GenerationTrace, ProgramWorkspace

# One chunk plus JSON indentation, quotes, and a trailing comma.
MAX_RENDERED_LINE = READABLE_CHUNK_CHARS + 100


def _snapshot(raw_trajectory: str) -> dict:
    return {
        "kind": "generation",
        "repo": "demo-project",
        "task_group": "default",
        "traces": [
            {
                "problem_id": "episode-1",
                "task": "Do the thing",
                "raw_trajectory": raw_trajectory,
            }
        ],
    }


class ReadablePromptRenderingTests(unittest.TestCase):
    def test_long_strings_chunk_into_bounded_parseable_lines(self) -> None:
        original = "x" * (READABLE_CHUNK_CHARS * 5 + 123)
        rendered = render_readable_json(_snapshot(original))

        self.assertLessEqual(
            max(len(line) for line in rendered.splitlines()),
            MAX_RENDERED_LINE,
        )
        decoded = json.loads(rendered)
        chunks = decoded["traces"][0]["raw_trajectory"]
        self.assertIsInstance(chunks, list)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), original)

    def test_short_strings_stay_unchunked(self) -> None:
        snapshot = _snapshot("short trajectory")
        self.assertEqual(json.loads(render_readable_json(snapshot)), snapshot)

    def test_generation_prompt_is_line_readable_and_explains_chunks(self) -> None:
        prompt = build_prompt(_snapshot("y" * (READABLE_CHUNK_CHARS * 400)))
        self.assertIn(CHUNKED_TEXT_NOTE, prompt)
        self.assertLessEqual(
            max(len(line) for line in prompt.splitlines()),
            MAX_RENDERED_LINE,
        )

    def test_support_review_prompt_is_line_readable(self) -> None:
        candidate = {"decision": "replace", "codes": [], "summary": "s" * 5000}
        prompt = build_support_review_prompt(
            _snapshot("z" * (READABLE_CHUNK_CHARS * 40)),
            candidate,
        )
        self.assertIn(CHUNKED_TEXT_NOTE, prompt)
        self.assertLessEqual(
            max(len(line) for line in prompt.splitlines()),
            MAX_RENDERED_LINE,
        )


class ConsecutiveFailureGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.program = self.root / "program"
        self.store_dir = self.root / "taxonomies"
        self.trace_root = self.root / "traces"
        self.workspace = ProgramWorkspace(self.program, repo="demo-project")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _append_pending(self, start: int, count: int) -> None:
        self.workspace.pending.append_many_with_names(
            GenerationTrace(
                problem_id=f"episode-{index}",
                task=f"Task {index}",
                raw_trajectory=f"Observed failure in completed episode {index}",
                metadata={"outcome": "hidden", "episode": index},
            )
            for index in range(start, start + count)
        )

    def _poll(self) -> str | None:
        return poll_learning_jobs(
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

    def _fail_active_job(self, job_id: str) -> None:
        job_dir = self.program / "learning_jobs" / job_id
        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        _fail_job(self.workspace, job_dir, job, "deterministic worker failure")

    def _consecutive_failures(self) -> int:
        learning = self.workspace.load().get("interactive_learning", {})
        return int(learning.get("consecutive_failures", 0))

    def test_requeue_parks_after_two_failures_and_resumes_on_reset(self) -> None:
        self._append_pending(1, 5)

        first = self._poll()
        self.assertIsNotNone(first)
        self._fail_active_job(first)
        self.assertEqual(self._consecutive_failures(), 1)

        # A failure raises retry_after_count by one threshold; add traces past
        # it so the retry backoff alone would allow each following poll. This
        # mirrors the production loop, where worker episodes kept adding
        # traces and re-arming generation despite deterministic failures.
        self._append_pending(6, 5)
        second = self._poll()
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        self._fail_active_job(second)
        self.assertEqual(self._consecutive_failures(), 2)

        self._append_pending(11, 5)
        self.assertIsNone(self._poll())
        job_dirs = list((self.program / "learning_jobs").glob("*/job.json"))
        self.assertEqual(len(job_dirs), 2)

        with self.workspace.locked_manifest() as manifest:
            manifest["interactive_learning"]["consecutive_failures"] = 0
        self.assertIsNotNone(self._poll())

    def test_direct_enqueue_failure_also_counts(self) -> None:
        self._append_pending(1, 5)
        job_id = enqueue_learning_job(
            self.workspace,
            kind="generation",
            store_dir=self.store_dir,
            trace_root=self.trace_root,
            task_group="default",
            conversation_id="conversation-1",
            codex_cli_path=sys.executable,
            launcher=lambda job_dir: None,
        )
        self._fail_active_job(job_id)
        self.assertEqual(self._consecutive_failures(), 1)


if __name__ == "__main__":
    unittest.main()
