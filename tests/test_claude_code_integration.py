"""Claude Code runtime-skin behavior."""

from __future__ import annotations

import json
import io
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from adamast.hosts.claude_code.config import ClaudeCodeConfig, parse_built_in_hooks
from adamast.hosts.claude_code.checkpoint import record_checkpoint
from adamast.hosts.claude_code.hooks import (
    post_tool_use,
    post_tool_use_failure,
    session_end,
    session_start,
    stop,
    task_completed,
    user_prompt_submit,
)
from adamast.hosts.claude_code.install import (
    REQUIRED_EVENTS,
    install,
    installed_claude_executable,
    main as install_main,
    verify_installed_hooks,
)
from adamast.hosts.claude_code.uninstall import uninstall
from adamast.hosts.claude_code.state import load_state
from adamast.hosts.claude_code.transcript import claude_thread_title
from adamast.core.evidence import EVIDENCE_FILE
from adamast.core.program import ProgramWorkspace
from adamast.core.traces import TRACE_FIELDS

ROOT = Path(__file__).resolve().parent.parent
STORE_DIR = ROOT / "tests" / "fixtures" / "taxonomies"


def append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": text}]
                    },
                }
            )
            + "\n"
        )


def append_user_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": text}],
                    },
                }
            )
            + "\n"
        )


def checkpoint_id(prompt: str) -> str:
    match = re.search(r"Checkpoint ID:\s*(\S+)", prompt)
    assert match
    return match.group(1)


def fired_reflection(cid: str, code: str = "MAST-12") -> str:
    return f"""AdaMAST reflection:
- Checkpoint ID: {cid}
- Observe: The trace shows a verification gap.
- Map:
  - {code} | exhibited | evidence: "the full suite was not run"
- Correlate: The missing verification genuinely constitutes this failure.
- Decide: change: run the full suite before proceeding
"""


def none_reflection(cid: str) -> str:
    return f"""AdaMAST reflection:
- Checkpoint ID: {cid}
- Observe: The trace is complete and verified.
- Map:
  - none apply | considered: MAST-12 | evidence: "the full suite passed"
- Correlate: No apparent match survives comparison with the evidence.
- Decide: no change needed, because verification is green
"""


class ClaudeCodeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trace_output = self.root / "program"
        self.transcript = self.root / "main.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        self.config = ClaudeCodeConfig(
            trace_output=self.trace_output,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            max_retries=3,
            failure_throttle_calls=2,
            failure_recency_seconds=0,
        )
        self.base = {
            "session_id": "session-1",
            "transcript_path": str(self.transcript),
            "cwd": str(self.root),
        }
        with patch.dict(os.environ, {"ADAMAST_DISABLE_DASHBOARD": "1"}):
            code, output = session_start.handle(
                {**self.base, "hook_event_name": "SessionStart"},
                self.config,
            )
        self.assertEqual(code, 0)
        self.start_output = output

    def tearDown(self):
        self.temp.cleanup()

    def test_claude_title_is_read_from_transcript_and_persisted(self):
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "type": "custom-title",
                        "sessionId": self.base["session_id"],
                        "customTitle": "AdaMAST repository review",
                    }
                )
                + "\n"
            )

        self.assertEqual(
            claude_thread_title(
                self.base["session_id"],
                transcript_path=self.transcript,
            ),
            "AdaMAST repository review",
        )
        with patch.dict(os.environ, {"ADAMAST_DISABLE_DASHBOARD": "1"}):
            session_start.handle(
                {**self.base, "hook_event_name": "SessionStart"},
                self.config,
            )
        state = load_state(self.trace_output, self.base["session_id"])
        self.assertEqual(state["conversation_title"], "AdaMAST repository review")
        self.assertEqual(state["conversation_host"], "claude_code")

    def test_private_checkpoint_releases_stop_without_visible_reflection(self):
        report = (
            "Checkpoint: CLAUDE_MONITOR_READY\n"
            "Relevant codes: none apply\n"
            "Evidence: the monitor and direct recorder were verified\n"
            "Next action: complete\n"
        )
        recorded = record_checkpoint(
            self.trace_output,
            self.base["session_id"],
            report,
            gate="stop",
        )

        code, output = stop.handle(
            {
                **self.base,
                "hook_event_name": "Stop",
                "prompt_id": "claude-prompt-42",
                "last_assistant_message": "The work is complete.",
            },
            self.config,
        )

        self.assertEqual((code, output), (0, ""))
        evidence = json.loads(
            (self.trace_output / EVIDENCE_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(len(evidence["checkpoints"]), 1)
        checkpoint = evidence["checkpoints"][0]
        self.assertEqual(checkpoint["checkpoint_id"], recorded["checkpoint_id"])
        self.assertEqual(checkpoint["turn_id"], "claude-prompt-42")
        self.assertEqual(checkpoint["source"], "claude_direct")

    def test_private_checkpoint_records_cited_taxonomy_code(self):
        state = load_state(self.trace_output, self.base["session_id"])
        cited = str(state["taxonomy"]["codes"][0]["id"])
        recorded = record_checkpoint(
            self.trace_output,
            self.base["session_id"],
            f"Checkpoint: FINAL_GATE\nRelevant codes: {cited}\n"
            "Evidence: the failing behavior was reproduced directly\n"
            "Next action: no further action required\n",
            gate="stop",
        )

        self.assertTrue(recorded["recorded"])
        evidence = json.loads(
            (self.trace_output / EVIDENCE_FILE).read_text(encoding="utf-8")
        )
        checkpoint = evidence["checkpoints"][0]
        self.assertEqual(checkpoint["fired_codes"], [cited])
        self.assertFalse(checkpoint["none_apply"])
        taxonomy_id = str(state["taxonomy_id"])
        code_record = evidence["taxonomies"][taxonomy_id]["codes"][cited]
        self.assertEqual(code_record["fire_count"], 1)

    def test_multiple_private_checkpoints_share_one_claude_prompt(self):
        first = record_checkpoint(
            self.trace_output,
            self.base["session_id"],
            "Checkpoint: TOOL_GATE\nRelevant codes: none apply\n"
            "Evidence: a failed tool was recovered\nNext action: continue\n",
            gate="tool_failure",
        )
        final = record_checkpoint(
            self.trace_output,
            self.base["session_id"],
            "Checkpoint: FINAL_GATE\nRelevant codes: none apply\n"
            "Evidence: final verification passed\nNext action: complete\n",
            gate="stop",
        )

        code, output = stop.handle(
            {
                **self.base,
                "hook_event_name": "Stop",
                "prompt_id": "claude-prompt-multi",
            },
            self.config,
        )

        self.assertEqual((code, output), (0, ""))
        evidence = json.loads(
            (self.trace_output / EVIDENCE_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(
            {item["checkpoint_id"] for item in evidence["checkpoints"]},
            {first["checkpoint_id"], final["checkpoint_id"]},
        )
        self.assertEqual(
            {item["turn_id"] for item in evidence["checkpoints"]},
            {"claude-prompt-multi"},
        )

    def test_private_checkpoint_is_consumed_by_only_one_gate_event(self):
        record_checkpoint(
            self.trace_output,
            self.base["session_id"],
            "Checkpoint: FIRST_TASK\nRelevant codes: none apply\n"
            "Evidence: the first sub-task was verified\nNext action: continue\n",
            gate="task_completed",
        )
        first = {
            **self.base,
            "hook_event_name": "TaskCompleted",
            "task_id": "task-one",
            "task_subject": "First task",
            "prompt_id": "claude-prompt-shared",
        }
        self.assertEqual(task_completed.handle(first, self.config), (0, ""))

        second = {
            **first,
            "task_id": "task-two",
            "task_subject": "Second task",
        }
        code, prompt = task_completed.handle(second, self.config)
        self.assertEqual(code, 2)
        self.assertIn("private checkpoint", prompt)
        self.assertIn("adamast-claude-checkpoint", prompt)
        self.assertIn('--gate "task_completed"', prompt)
        self.assertNotIn("Relevant codes", prompt)
        self.assertNotIn("Evidence:", prompt)

    def test_missing_private_gate_never_requests_visible_reflection(self):
        code, prompt = stop.handle(
            {**self.base, "hook_event_name": "Stop"},
            self.config,
        )
        self.assertEqual(code, 2)
        self.assertIn("private checkpoint", prompt)
        self.assertIn("adamast-claude-checkpoint", prompt)
        self.assertIn('--gate "stop"', prompt)
        for private_field in (
            "AdaMAST reflection",
            "Checkpoint ID:",
            "Relevant codes:",
            "Evidence:",
            "Final AdaMAST status:",
        ):
            self.assertNotIn(private_field, prompt)

    def test_claude_opens_the_shared_monitor_once(self):
        base = {**self.base, "session_id": "claude-monitor-session"}
        monitor_url = (
            "http://127.0.0.1:8765/"
            "?project=demo&conversation=claude-monitor-session"
        )
        with (
            patch(
                "adamast.dashboard.server.ensure_dashboard",
                return_value=monitor_url,
            ) as ensure,
            patch(
                "adamast.hosts.claude_code.runtime.webbrowser.open",
                return_value=True,
            ) as open_browser,
        ):
            code, output = session_start.handle(
                {**base, "hook_event_name": "SessionStart"},
                self.config,
            )

        self.assertEqual(code, 0)
        ensure.assert_called_once()
        open_browser.assert_called_once_with(monitor_url)
        self.assertIn(monitor_url, output["hookSpecificOutput"]["additionalContext"])
        state = load_state(self.trace_output, base["session_id"])
        self.assertTrue(state["monitor_opened"])
        self.assertEqual(state["dashboard_url"], monitor_url)

    def test_legacy_max_retries_maps_to_repair_rounds(self):
        config = ClaudeCodeConfig(
            trace_output=self.trace_output,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            max_retries=5,
        )
        self.assertEqual(config.repair_rounds, 5)
        self.assertEqual(config.format_retries, 2)
        data = config.to_dict()
        self.assertEqual(data["max_retries"], 5)
        self.assertEqual(data["repair_rounds"], 5)
        self.assertEqual(data["format_retries"], 2)

        path = self.root / "legacy-atlas.json"
        path.write_text(
            json.dumps(
                {
                    "trace_output": str(self.trace_output),
                    "adamast_model": "test-model",
                    "max_retries": 3,
                }
            ),
            encoding="utf-8",
        )
        loaded = ClaudeCodeConfig.load(path)
        self.assertEqual(loaded.repair_rounds, 3)
        self.assertEqual(loaded.format_retries, 2)

    def test_traces_are_redacted_by_default(self):
        append_text(
            self.transcript,
            "Set api_key=super-secret-token-123 before the run.",
        )
        event = {
            **self.base,
            "hook_event_name": "SessionEnd",
            "reason": "prompt_input_exit",
        }
        code, _message = session_end.handle(event, self.config)
        self.assertEqual(code, 0)
        pending = list((self.trace_output / "pending").glob("trace-*.json"))
        self.assertEqual(len(pending), 1)
        body = pending[0].read_text(encoding="utf-8")
        self.assertNotIn("super-secret-token-123", body)
        self.assertIn("[REDACTED]", body)

    def test_trace_redaction_can_be_disabled(self):
        config = ClaudeCodeConfig(
            trace_output=self.trace_output,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            redact_traces=False,
        )
        base = {**self.base, "session_id": "session-noredact"}
        with patch.dict(os.environ, {"ADAMAST_DISABLE_DASHBOARD": "1"}):
            session_start.handle(
                {**base, "hook_event_name": "SessionStart"}, config
            )
        append_text(self.transcript, "token check api_key=keepme-value")
        code, _message = session_end.handle(
            {**base, "hook_event_name": "SessionEnd", "reason": "exit"},
            config,
        )
        self.assertEqual(code, 0)
        pending = list((self.trace_output / "pending").glob("trace-*.json"))
        bodies = "".join(p.read_text(encoding="utf-8") for p in pending)
        self.assertIn("keepme-value", bodies)

    def test_finalize_crash_does_not_duplicate_trace(self):
        # Regression: the trace was persisted inside end_session, but
        # trace_captured was only saved after learning completed, so a crash
        # or hook-timeout kill made the retry record a second copy.
        event = {
            **self.base,
            "hook_event_name": "SessionEnd",
            "reason": "prompt_input_exit",
        }
        append_text(self.transcript, "Some session activity.")
        with patch(
            "adamast.hosts.claude_code.runtime.end_session",
            side_effect=RuntimeError("learning crashed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "learning crashed"):
                session_end.handle(event, self.config)
        pending = list((self.trace_output / "pending").glob("trace-*.json"))
        self.assertEqual(len(pending), 1)
        state = load_state(self.trace_output, self.base["session_id"])
        self.assertTrue(state["trace_captured"])
        self.assertFalse(state.get("finished"))

        # The retry completes the lifecycle without re-recording the trace.
        code, _message = session_end.handle(event, self.config)
        self.assertEqual(code, 0)
        pending = list((self.trace_output / "pending").glob("trace-*.json"))
        self.assertEqual(len(pending), 1)
        state = load_state(self.trace_output, self.base["session_id"])
        self.assertTrue(state["finished"])
        self.assertEqual(state["trace_capture"]["persisted_traces"], 1)

    def test_hooks_force_background_learning(self):
        # Learning must never run inline under Claude Code's per-hook
        # timeout, regardless of the configured *_stops flags.
        config = ClaudeCodeConfig(
            trace_output=self.trace_output,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            generation_stops=True,
            refinement_stops=True,
        )
        base = {**self.base, "session_id": "session-background"}
        with patch.dict(os.environ, {"ADAMAST_DISABLE_DASHBOARD": "1"}):
            session_start.handle(
                {**base, "hook_event_name": "SessionStart"}, config
            )
        state = load_state(self.trace_output, base["session_id"])
        self.assertFalse(state["lifecycle"]["generation_stops"])
        self.assertFalse(state["lifecycle"]["refinement_stops"])

        captured = {}
        import adamast.hosts.claude_code.runtime as rt

        real_end_session = rt.end_session

        def spy(session, **kwargs):
            captured["session"] = session
            return real_end_session(session, **kwargs)

        event = {
            **base,
            "hook_event_name": "SessionEnd",
            "reason": "prompt_input_exit",
        }
        with patch(
            "adamast.hosts.claude_code.runtime.end_session",
            side_effect=spy,
        ):
            code, _message = session_end.handle(event, config)
        self.assertEqual(code, 0)
        self.assertFalse(captured["session"].generation_stops)
        self.assertFalse(captured["session"].refinement_stops)

    def test_post_tool_filters_and_throttles_both_failure_sources(self):
        success = {
            **self.base,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_response": {"stdout": "12 tests passed", "exitCode": 0},
        }
        self.assertEqual(post_tool_use.handle(success, self.config), (0, None))

        failed_test = {
            **success,
            "tool_response": {
                "stdout": "FAILED test_x - AssertionError",
                "exitCode": 0,
            },
        }
        code, output = post_tool_use.handle(failed_test, self.config)
        self.assertEqual(code, 0)
        self.assertIn(
            "MAST-12",
            output["hookSpecificOutput"]["additionalContext"],
        )
        self.assertEqual(post_tool_use.handle(failed_test, self.config), (0, None))

        actual = {
            **self.base,
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "error": "process failed with exit code 2",
        }
        code, output = post_tool_use_failure.handle(actual, self.config)
        self.assertEqual(code, 0)
        self.assertIsNotNone(output)

    def test_zero_tool_path_can_process_task_completed_and_stop(self):
        task = {
            **self.base,
            "hook_event_name": "TaskCompleted",
            "task_id": "zero-tools",
            "task_subject": "Explicit task with no tools",
        }
        record_checkpoint(
            self.trace_output,
            self.base["session_id"],
            "Checkpoint: ZERO_TOOLS_TASK\nRelevant codes: none apply\n"
            "Evidence: the sub-task was checked\nNext action: continue\n",
            gate="task_completed",
        )
        self.assertEqual(task_completed.handle(task, self.config)[0], 0)

        stop_event = {
            **self.base,
            "hook_event_name": "Stop",
            "stop_hook_active": False,
            "last_assistant_message": "Done.",
        }
        record_checkpoint(
            self.trace_output,
            self.base["session_id"],
            "Checkpoint: ZERO_TOOLS_STOP\nRelevant codes: none apply\n"
            "Evidence: final response checked\nNext action: complete\n",
            gate="stop",
        )
        self.assertEqual(stop.handle(stop_event, self.config)[0], 0)

    def test_successful_stop_records_one_canonical_generation_trace(self):
        append_user_text(self.transcript, "Implement the Claude adapter.")
        append_text(self.transcript, "Implemented and verified.")
        event = {
            **self.base,
            "hook_event_name": "Stop",
            "stop_hook_active": False,
            "last_assistant_message": "Done.",
        }
        record_checkpoint(
            self.trace_output,
            self.base["session_id"],
            "Checkpoint: TRACE_READY\nRelevant codes: none apply\n"
            "Evidence: tests passed\nNext action: complete\n",
            gate="stop",
        )
        self.assertEqual(stop.handle(event, self.config)[0], 0)
        traces = list(ProgramWorkspace(self.trace_output).pending.iter_traces())
        self.assertEqual(len(traces), 1)
        trace = traces[0]
        self.assertEqual(tuple(trace.to_dict()), TRACE_FIELDS)
        self.assertEqual(trace.task, "Implement the Claude adapter.")
        self.assertIn("Implemented and verified.", trace.raw_trajectory)
        self.assertIn('"type": "assistant"', trace.raw_trajectory)
        self.assertEqual(trace.metadata["harness"], "claude_code")
        state = load_state(self.trace_output, self.base["session_id"])
        self.assertTrue(state["trace_captured"])
        self.assertEqual(state["trace_capture"]["persisted_traces"], 1)

    def test_successive_completed_turns_become_distinct_episode_traces(self):
        def complete_turn(task: str, answer: str) -> None:
            append_user_text(self.transcript, task)
            event = {
                **self.base,
                "hook_event_name": "Stop",
                "stop_hook_active": False,
                "last_assistant_message": answer,
            }
            if load_state(self.trace_output, self.base["session_id"]).get("finished"):
                user_prompt_submit.handle(
                    {**event, "hook_event_name": "UserPromptSubmit", "prompt": task},
                    self.config,
                )
            append_text(self.transcript, answer)
            record_checkpoint(
                self.trace_output,
                self.base["session_id"],
                f"Checkpoint: TURN_{task}\nRelevant codes: none apply\n"
                "Evidence: targeted verification passed\nNext action: complete\n",
                gate="stop",
            )
            self.assertEqual(stop.handle(event, self.config)[0], 0)

        complete_turn("FIRST CLAUDE TASK MARKER", "FIRST CLAUDE ANSWER MARKER")
        complete_turn("SECOND CLAUDE TASK MARKER", "SECOND CLAUDE ANSWER MARKER")
        duplicate = stop.handle(
            {
                **self.base,
                "hook_event_name": "Stop",
                "stop_hook_active": False,
                "last_assistant_message": "SECOND CLAUDE ANSWER MARKER",
            },
            self.config,
        )
        self.assertEqual(duplicate, (0, "AdaMAST episode already committed."))

        traces = sorted(
            ProgramWorkspace(self.trace_output).pending.iter_traces(),
            key=lambda trace: trace.metadata["episode_sequence"],
        )
        self.assertEqual(len(traces), 2)
        self.assertEqual(
            [trace.metadata["episode_sequence"] for trace in traces],
            [1, 2],
        )
        self.assertEqual(traces[0].task, "FIRST CLAUDE TASK MARKER")
        self.assertEqual(traces[1].task, "SECOND CLAUDE TASK MARKER")
        self.assertIn("FIRST CLAUDE ANSWER MARKER", traces[0].raw_trajectory)
        self.assertNotIn("FIRST CLAUDE ANSWER MARKER", traces[1].raw_trajectory)
        self.assertIn("SECOND CLAUDE ANSWER MARKER", traces[1].raw_trajectory)
        state = load_state(self.trace_output, self.base["session_id"])
        self.assertEqual(state["episode_sequence"], 2)
        self.assertEqual(state["conversation_taxonomy_root"], "mast")

    def test_five_completed_episodes_queue_exactly_one_native_generation_job(self):
        config = replace(
            self.config,
            learning_backend="claude_subagent",
            generation_threshold=5,
            claude_cli_path=Path(sys.executable),
        )
        workspace = ProgramWorkspace(self.trace_output)
        with workspace.locked_manifest() as manifest:
            manifest["generation"]["retry_after_count"] = 5

        with (
            patch(
                "adamast.hosts.claude_code.runtime.enqueue_claude_learning_job",
                return_value="claude-generation-five",
            ) as enqueue,
            patch(
                "adamast.learning.generation._adamast_generate",
                side_effect=AssertionError("provider generation must not run"),
            ) as provider,
        ):
            for index in range(1, 6):
                task = f"CLAUDE TASK {index}"
                answer = f"CLAUDE ANSWER {index}"
                append_user_text(self.transcript, task)
                event = {
                    **self.base,
                    "hook_event_name": "Stop",
                    "stop_hook_active": False,
                    "last_assistant_message": answer,
                }
                if load_state(
                    self.trace_output, self.base["session_id"]
                ).get("finished"):
                    user_prompt_submit.handle(
                        {
                            **event,
                            "hook_event_name": "UserPromptSubmit",
                            "prompt": task,
                        },
                        config,
                    )
                append_text(self.transcript, answer)
                record_checkpoint(
                    self.trace_output,
                    self.base["session_id"],
                    f"Checkpoint: TURN_{index}\nRelevant codes: none apply\n"
                    "Evidence: targeted verification passed\n"
                    "Next action: complete\n",
                    gate="stop",
                )
                self.assertEqual(stop.handle(event, config)[0], 0)

        self.assertEqual(enqueue.call_count, 1)
        self.assertEqual(enqueue.call_args.kwargs["kind"], "generation")
        provider.assert_not_called()
        self.assertEqual(len(list(workspace.pending.iter_traces())), 5)

    def test_session_end_captures_once_when_stop_did_not_finish(self):
        append_user_text(self.transcript, "Investigate an interrupted task.")
        append_text(self.transcript, "Partial work before interruption.")
        event = {
            **self.base,
            "hook_event_name": "SessionEnd",
            "reason": "other",
        }
        self.assertEqual(session_end.handle(event, self.config)[0], 0)
        self.assertEqual(session_end.handle(event, self.config), (0, None))
        workspace = ProgramWorkspace(self.trace_output)
        self.assertEqual(workspace.pending.count(), 1)
        self.assertEqual(workspace.load()["active_sessions"], [])


class ClaudeCodeInstallerTests(unittest.TestCase):
    def test_config_round_trip_preserves_runtime_environment_and_trace_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "config.json"
            config = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="gpt-5",
                store_dir=root / "store",
                trace_root=root / "learning-traces",
                dashboard=False,
                openai_base_url="http://127.0.0.1:8742/v1",
                openai_api_key_env="ADAMAST_PROXY_KEY",
                generation_threshold=7,
                generation_stops=True,
                skip_judge=True,
                k_init=11,
                k=21,
                refinement_stops=True,
                advanced_refinement=True,
            )
            path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            loaded = ClaudeCodeConfig.load(path)
            self.assertEqual(loaded.trace_root, (root / "learning-traces").resolve())
            self.assertFalse(loaded.dashboard)
            self.assertEqual(
                loaded.openai_base_url,
                "http://127.0.0.1:8742/v1",
            )
            self.assertEqual(loaded.openai_api_key_env, "ADAMAST_PROXY_KEY")
            self.assertEqual(loaded.generation_threshold, 7)
            self.assertTrue(loaded.generation_stops)
            self.assertTrue(loaded.skip_judge)
            self.assertEqual(loaded.k_init, 11)
            self.assertEqual(loaded.k, 21)
            self.assertTrue(loaded.refinement_stops)
            self.assertTrue(loaded.advanced_refinement)
            self.assertNotIn(
                "local-proxy",
                path.read_text(encoding="utf-8"),
            )

    def test_plaintext_credential_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "trace_output": str(Path(td) / "program"),
                        "adamast_model": "gpt-5",
                        "openai_api_key": "secret-value",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "plaintext"):
                ClaudeCodeConfig.load(path)

    def test_executable_discovery_prefers_explicit_override_then_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            explicit = root / "explicit-claude"
            from_path = root / "path-claude"
            explicit.write_bytes(b"explicit")
            from_path.write_bytes(b"path")
            with (
                patch.dict(
                    os.environ,
                    {"CLAUDE_CODE_EXECUTABLE": str(explicit)},
                    clear=False,
                ),
                patch(
                    "adamast.hosts.claude_code.install.shutil.which",
                    return_value=str(from_path),
                ),
            ):
                self.assertEqual(installed_claude_executable(), explicit.resolve())
            with (
                patch.dict(
                    os.environ,
                    {"CLAUDE_CODE_EXECUTABLE": ""},
                    clear=False,
                ),
                patch(
                    "adamast.hosts.claude_code.install.shutil.which",
                    return_value=str(from_path),
                ),
            ):
                self.assertEqual(installed_claude_executable(), from_path.resolve())

    def test_session_start_uses_configured_lifecycle_controls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "trace.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="gpt-5",
                store_dir=STORE_DIR,
                dashboard=False,
                generation_threshold=7,
                generation_stops=True,
                skip_judge=True,
                k_init=11,
                k=21,
                refinement_stops=True,
                advanced_refinement=True,
            )
            code, _ = session_start.handle(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "configured",
                    "transcript_path": str(transcript),
                    "cwd": str(root),
                },
                config,
            )
            self.assertEqual(code, 0)
            state = load_state(root / "program", "configured")
            self.assertEqual(state["lifecycle"]["generation_threshold"], 7)
            self.assertTrue(state["lifecycle"]["skip_judge"])
            self.assertEqual(state["lifecycle"]["k_init"], 11)
            self.assertEqual(state["lifecycle"]["k"], 21)
            self.assertTrue(state["lifecycle"]["advanced_refinement"])
            # The *_stops flags are deliberately NOT honored on hook paths:
            # learning must run in background workers, never inline under
            # Claude Code's per-hook timeout.
            self.assertFalse(state["lifecycle"]["generation_stops"])
            self.assertFalse(state["lifecycle"]["refinement_stops"])

    def test_hook_contract_verifier_accepts_supported_version(self):
        with tempfile.TemporaryDirectory() as td:
            executable = Path(td) / "claude"
            executable.write_bytes(
                b"\n".join(
                    [event.encode() for event in REQUIRED_EVENTS]
                    + [
                        b"prevent task completion",
                        b"show stderr to subagent and continue having it run",
                        b"show stderr to model and continue conversation",
                        b"hookSpecificOutput.additionalContext",
                    ]
                )
            )
            completed = unittest.mock.Mock(stdout="2.1.185 (Claude Code)\n")
            with patch(
                "adamast.hosts.claude_code.install.subprocess.run",
                return_value=completed,
            ):
                version = verify_installed_hooks(executable)

        self.assertRegex(version, r"\d+\.\d+\.\d+")

    def test_installer_registers_all_events_without_duplication(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
            )
            first = install(root, config, verify=False)
            install(root, config, verify=False)
            worker_agent = Path(first["taxonomy_worker_agent"])
            # install() resolves project_dir, expanding Windows 8.3 short
            # names (the CI runner's TEMP is C:\Users\RUNNER~1\...), so the
            # expected path must be resolved the same way.
            self.assertEqual(
                worker_agent,
                root.resolve() / ".claude" / "agents" / "adamast-taxonomy-worker.md",
            )
            agent_text = worker_agent.read_text(encoding="utf-8")
            self.assertIn("name: adamast-taxonomy-worker", agent_text)
            self.assertIn("background: true", agent_text)
            self.assertIn("tools: Read", agent_text)
            settings = json.loads(
                Path(first["settings"]).read_text(encoding="utf-8")
            )
            self.assertEqual(set(settings["hooks"]), set(REQUIRED_EVENTS))
            for event in REQUIRED_EVENTS:
                self.assertEqual(len(settings["hooks"][event]), 1)
                command = settings["hooks"][event][0]["hooks"][0]["command"]
                # Module invocation, never a dispatcher file path: the path
                # goes stale when the install mode or location changes, and
                # then every hook event fails.
                self.assertIn(
                    "-m adamast.hosts.claude_code.dispatcher",
                    command,
                )
                self.assertNotIn("dispatcher.py", command)
                if os.name == "nt":
                    self.assertNotIn("\\", command)

    def test_installer_can_filter_built_in_hooks_and_tool_matchers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
                built_in_hooks=parse_built_in_hooks({
                    "SubagentStop": False,
                    "PostToolUse": ["Bash", "Edit"],
                    "PostToolUseFailure": ["Bash"],
                }),
            )
            result = install(root, config, verify=False)
            settings = json.loads(
                Path(result["settings"]).read_text(encoding="utf-8")
            )

            self.assertNotIn("SubagentStop", settings["hooks"])
            self.assertNotIn("SubagentStop", result["events"])
            self.assertEqual(
                {entry["matcher"] for entry in settings["hooks"]["PostToolUse"]},
                {"Bash", "Edit"},
            )
            self.assertEqual(
                [entry["matcher"] for entry in settings["hooks"]["PostToolUseFailure"]],
                ["Bash"],
            )
            for event in {"SessionStart", "SessionEnd", "Stop", "TaskCompleted"}:
                self.assertIn(event, settings["hooks"])
                self.assertNotIn("matcher", settings["hooks"][event][0])

    def test_installer_refuses_invalid_existing_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            claude = root / ".claude"
            claude.mkdir()
            settings = claude / "settings.local.json"
            settings.write_text("{broken", encoding="utf-8")
            config = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
            )
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                install(root, config, verify=False)
            self.assertEqual(settings.read_text(encoding="utf-8"), "{broken")
            self.assertFalse((claude / "adamast.json").exists())

    def test_verification_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
            )
            with (
                patch(
                    "adamast.hosts.claude_code.install.verify_installed_hooks",
                    side_effect=RuntimeError("missing hook"),
                ),
                self.assertRaisesRegex(RuntimeError, "missing hook"),
            ):
                install(root, config)
            self.assertFalse((root / ".claude").exists())

    def test_user_level_install_is_zero_config_and_native_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with (
                patch(
                    "adamast.hosts.claude_code.install.Path.home",
                    return_value=root,
                ),
                patch(
                    "adamast.hosts.claude_code.install.verify_installed_hooks",
                    return_value="9.9.9",
                ),
                redirect_stdout(io.StringIO()) as output,
                redirect_stderr(io.StringIO()),
            ):
                code = install_main(["--user-level"])

            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["scope"], "user")
            config = ClaudeCodeConfig.load(
                root / ".claude" / "adamast.json"
            )
            self.assertEqual(
                config.trace_output.resolve(),
                (root / ".adamast" / "interactive").resolve(),
            )
            self.assertEqual(config.adamast_model, "interactive-session")
            self.assertEqual(config.project_scope, "auto")
            self.assertEqual(config.session_selector, "prompt")
            self.assertEqual(config.selector_surface, "browser")
            self.assertEqual(config.learning_backend, "claude_subagent")
            self.assertIsNone(config.openai_api_key_env)

    def test_user_level_install_rejects_project_target(self):
        with self.assertRaises(SystemExit):
            install_main(["--user-level", "--project-dir", "."])

    def test_empty_inherit_form_resolves_picker_at_install_time(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            captured = {}

            def fake_install(_project, config, **_kwargs):
                captured["inherit"] = config.inherit
                return {}

            with (
                patch(
                    "adamast.hosts.claude_code.install.resolver.resolve",
                    return_value="tax-django-orm-001",
                ) as resolve,
                patch(
                    "adamast.hosts.claude_code.install.install",
                    side_effect=fake_install,
                ),
            ):
                self.assertEqual(
                    install_main(
                        [
                            "--project-dir",
                            str(root),
                            "--trace-output",
                            str(root / "program"),
                            "--adamast-model",
                            "gpt-5",
                            "--store-dir",
                            str(STORE_DIR),
                            "--inherit",
                        ]
                    ),
                    0,
                )
            self.assertEqual(captured["inherit"], "tax-django-orm-001")
            self.assertEqual(resolve.call_count, 1)

    def test_inherit_pick_resolves_picker_at_install_time(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            captured = {}

            def fake_install(_project, config, **_kwargs):
                captured["inherit"] = config.inherit
                return {}

            with (
                patch(
                    "adamast.hosts.claude_code.install.resolver.resolve",
                    return_value="tax-django-orm-001",
                ) as resolve,
                patch(
                    "adamast.hosts.claude_code.install.install",
                    side_effect=fake_install,
                ),
            ):
                self.assertEqual(
                    install_main(
                        [
                            "--project-dir",
                            str(root),
                            "--trace-output",
                            str(root / "program"),
                            "--adamast-model",
                            "gpt-5",
                            "--store-dir",
                            str(STORE_DIR),
                            "--inherit-pick",
                        ]
                    ),
                    0,
                )
            self.assertEqual(captured["inherit"], "tax-django-orm-001")
            self.assertEqual(resolve.call_count, 1)

    def test_uninstall_removes_only_adamast_registration_and_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
            )
            info = install(root, config, verify=False)
            settings_path = Path(info["settings"])
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            settings["hooks"]["Stop"].append(
                {
                    "hooks": [
                        {"type": "command", "command": "other-tool stop"}
                    ]
                }
            )
            settings_path.write_text(
                json.dumps(settings),
                encoding="utf-8",
            )

            result = uninstall(root)

            self.assertTrue(result["config_removed"])
            self.assertTrue(result["taxonomy_worker_agent_removed"])
            self.assertFalse(Path(info["taxonomy_worker_agent"]).exists())
            remaining = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                remaining["hooks"]["Stop"],
                [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "other-tool stop",
                            }
                        ]
                    }
                ],
            )
            self.assertNotIn("SessionStart", remaining["hooks"])

    def test_uninstall_preserves_adamast_named_unrelated_hook(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
            )
            info = install(root, config, verify=False)
            settings_path = Path(info["settings"])
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            settings["hooks"]["Stop"].append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "my-adamast-failure-modes-notifier stop",
                        }
                    ]
                }
            )
            settings_path.write_text(json.dumps(settings), encoding="utf-8")

            uninstall(root)

            remaining = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertIn(
                "my-adamast-failure-modes-notifier",
                json.dumps(remaining),
            )


if __name__ == "__main__":
    unittest.main()
