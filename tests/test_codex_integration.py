"""Codex hook integration behavior."""

from __future__ import annotations

import json
import io
import re
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from adamast.hosts.codex.config import CodexConfig, parse_codex_hooks
from adamast.hosts.codex.checkpoint import record_checkpoint
from adamast.hosts.codex.dispatcher import (
    _is_internal_codex_event,
    _merge_learning_context,
    _merge_notices,
    main as dispatcher_main,
)
from adamast.hosts.codex.install import (
    SKILL_NAME,
    install,
    install_skill,
    main as install_main,
)
from adamast.hosts.codex.runtime import (
    _next_action_requires_repair,
    post_tool_use,
    session_start,
    stop,
    subagent_stop,
    user_prompt_submit,
)
from adamast.hosts.codex.state import load_state, save_state
from adamast.hosts.codex.transcript import (
    codex_thread_title,
    first_user_message,
    read_raw_transcript,
    resolve_conversation_title,
)
from adamast.hosts.codex.uninstall import uninstall, uninstall_skill
from adamast.core.evidence import EVIDENCE_FILE
from adamast.core.program import ProgramWorkspace
from adamast.core.traces import GenerationTrace

ROOT = Path(__file__).resolve().parent.parent
STORE_DIR = ROOT / "tests" / "fixtures" / "taxonomies"


def append_text(path: Path, text: str, *, role: str = "assistant") -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": role,
                    "message": {
                        "role": role,
                        "content": [{"type": "text", "text": text}],
                    },
                }
            )
            + "\n"
        )


def append_item(path: Path, item: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item) + "\n")


def checkpoint_id(prompt: str) -> str:
    match = re.search(r"Checkpoint ID:\s*(\S+)", prompt)
    assert match
    return match.group(1)


def passing_report(prompt: str) -> str:
    cid = checkpoint_id(prompt)
    return f"""AdaMAST reflection:
- Checkpoint ID: {cid}
- Observe: The requested turn was completed and checked.
- Correlate: No evidence-supported failure remains.
- Map:
  - none apply | considered: MAST-12 | evidence: "checked"
- Decide: no change needed, because verification passed.

Final AdaMAST status: READY_TO_SUBMIT
Codes checked: none
Evidence: targeted verification passed
Repair attempts used: 0
Final decision: submit
"""


def compact_report(
    checkpoint: str = "task complete",
    *,
    codes: str = "none apply",
    evidence: str = "targeted verification passed",
    next_action: str = "complete",
) -> str:
    return f"""Checkpoint: {checkpoint}
Relevant codes: {codes}
Evidence: {evidence}
Next action: {next_action}
"""


class CodexIntegrationTests(unittest.TestCase):
    def test_conversation_title_uses_codex_session_index(self):
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp)
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": "conversation-title-test",
                        "thread_name": "Review the AdaMAST runtime",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                codex_thread_title(
                    "conversation-title-test",
                    codex_home=codex_home,
                ),
                "Review the AdaMAST runtime",
            )
            self.assertEqual(
                resolve_conversation_title(
                    "conversation-title-test",
                    existing="Conversation conversa",
                    prompt="A much longer first prompt",
                    codex_home=codex_home,
                ),
                "Review the AdaMAST runtime",
            )

    def test_session_state_persists_codex_task_title(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            codex_home = root / ".codex"
            codex_home.mkdir()
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": "named-session",
                        "thread_name": "Repair the release workflow",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config = self.base_config(root)
            event = {
                "session_id": "named-session",
                "cwd": str(root),
                "hook_event_name": "SessionStart",
            }

            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}):
                session_start(event, config)

            state = load_state(config.trace_output, "named-session")
            self.assertEqual(
                state["conversation_title"],
                "Repair the release workflow",
            )

    def test_compact_next_action_negation_is_ready(self):
        self.assertFalse(
            _next_action_requires_repair("no further action required")
        )
        self.assertFalse(_next_action_requires_repair("no repair required"))
        self.assertTrue(_next_action_requires_repair("repair required"))
        self.assertTrue(_next_action_requires_repair("report unresolved"))

    def base_config(self, root: Path) -> CodexConfig:
        return CodexConfig(
            trace_output=root / "program",
            adamast_model="test-model",
            store_dir=STORE_DIR,
            dashboard=False,
        )

    def selector_config(
        self,
        root: Path,
        *,
        store_dir: Path | None = None,
    ) -> CodexConfig:
        return replace(
            self.base_config(root),
            store_dir=store_dir or STORE_DIR,
            session_selector="prompt",
            selector_surface="inline",
        )

    def test_default_hooks_can_be_customized(self):
        specs = parse_codex_hooks(
            {
                "SubagentStop": False,
                "PostToolUse": {"matchers": ["Bash", "Edit|Write"]},
            }
        )
        by_event = {spec.event: spec for spec in specs}
        self.assertTrue(by_event["UserPromptSubmit"].enabled)
        self.assertEqual(
            by_event["SessionStart"].matchers,
            ("startup|resume|compact",),
        )
        self.assertFalse(by_event["SubagentStop"].enabled)
        self.assertEqual(by_event["PostToolUse"].matchers, ("Bash", "Edit|Write"))

    def test_learning_notice_merges_with_existing_gate_message(self):
        output = _merge_notices(
            {"continue": True, "systemMessage": "AdaMAST reflection accepted."},
            ["AdaMAST taxonomy generation triggered"],
        )
        self.assertTrue(output["continue"])
        self.assertEqual(
            output["systemMessage"],
            "AdaMAST reflection accepted.\n\nAdaMAST taxonomy generation triggered",
        )
        self.assertIn(
            "show the user this state change once",
            output["hookSpecificOutput"]["additionalContext"],
        )
        self.assertIn(
            "AdaMAST taxonomy generation triggered",
            output["hookSpecificOutput"]["additionalContext"],
        )

    def test_post_tool_use_is_a_silent_poll(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.base_config(root)
            event = {
                "session_id": "silent-tool-poll",
                "cwd": str(root),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)

            output = post_tool_use(
                {
                    **event,
                    "hook_event_name": "PostToolUse",
                    "tool_response": {
                        "success": True,
                        "output": "source mentions FAILED and AssertionError",
                    },
                },
                config,
            )

            self.assertIsNone(output)
            state = load_state(config.trace_output, event["session_id"])
            self.assertEqual(state["failure"]["call_index"], 1)

    def test_learning_dispatch_preserves_existing_hook_context(self):
        output = _merge_learning_context(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "Existing AdaMAST standing context.",
                }
            },
            "Launch the native taxonomy subagent.",
        )

        specific = output["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "UserPromptSubmit")
        self.assertEqual(
            specific["additionalContext"],
            "Existing AdaMAST standing context.\n\n"
            "Launch the native taxonomy subagent.",
        )
        self.assertTrue(output["continue"])

    def test_dispatcher_ignores_codex_internal_memory_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            codex_home = root / ".codex"
            memories = codex_home / "memories"
            memories.mkdir(parents=True)
            config = self.base_config(root)
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps(config.to_dict()),
                encoding="utf-8",
            )
            event = {
                "hook_event_name": "SessionStart",
                "session_id": "internal-memory-task",
                "cwd": str(memories),
            }
            stdout = io.StringIO()

            with (
                patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}),
                patch("sys.stdin", io.StringIO(json.dumps(event))),
                redirect_stdout(stdout),
            ):
                code = dispatcher_main(["--config", str(config_path)])
                self.assertTrue(_is_internal_codex_event(event))
                self.assertFalse(
                    _is_internal_codex_event(
                        {**event, "cwd": str(root / "project")}
                    )
                )

            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertFalse(config.trace_output.exists())

    def test_dispatcher_does_not_claim_learning_before_browser_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(
                self.selector_config(root),
                selector_surface="browser",
                learning_backend="codex_subagent",
            )
            config_path = root / "adamast.json"
            config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            event = {
                "hook_event_name": "SessionStart",
                "session_id": "internal-child-session",
                "cwd": str(root),
                "source": "startup",
            }
            stdout = io.StringIO()

            with (
                patch("sys.stdin", io.StringIO(json.dumps(event))),
                redirect_stdout(stdout),
                patch("adamast.hosts.codex.dispatcher.poll_learning_jobs") as poll,
                patch("adamast.hosts.codex.dispatcher.claim_learning_job") as claim,
            ):
                code = dispatcher_main(["--config", str(config_path)])

            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                load_state(config.trace_output, event["session_id"]),
                {},
            )
            poll.assert_not_called()
            claim.assert_not_called()

    def test_dispatcher_polls_and_claims_missed_generation_on_user_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(
                self.base_config(root),
                learning_backend="codex_subagent",
                generation_threshold=5,
            )
            config_path = root / "adamast.json"
            config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            workspace = ProgramWorkspace(config.trace_output, repo="demo")
            workspace.pending.append_many_with_names(
                GenerationTrace(
                    problem_id=f"episode-{index}",
                    task=f"Task {index}",
                    raw_trajectory=f"Completed trace {index}",
                )
                for index in range(5)
            )
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            event = {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "poll-session",
                "cwd": str(root),
                "transcript_path": str(transcript),
                "prompt": "Continue the main task.",
            }
            stdout = io.StringIO()
            with patch("sys.stdin", io.StringIO(json.dumps(event))), redirect_stdout(
                stdout
            ):
                code = dispatcher_main(["--config", str(config_path)])

            self.assertEqual(code, 0)
            output = json.loads(stdout.getvalue())
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("show the user this state change once", context)
            self.assertIn("taxonomy generation triggered", context)
            self.assertIn("AdaMAST native taxonomy learning is ready", context)
            self.assertIn("SUBAGENT TASK BEGIN", context)
            job_path = next(
                (config.trace_output / "learning_jobs").glob("*/job.json")
            )
            self.assertEqual(
                json.loads(job_path.read_text(encoding="utf-8"))["state"],
                "claimed",
            )

    def test_dispatcher_never_claims_next_phase_on_subagent_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(
                self.base_config(root),
                learning_backend="codex_subagent",
            )
            config_path = root / "adamast.json"
            config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            event = {
                "hook_event_name": "SubagentStop",
                "session_id": "parent-session",
                "cwd": str(root),
                "last_assistant_message": "Subagent work finished.",
            }
            stdout = io.StringIO()

            with (
                patch("sys.stdin", io.StringIO(json.dumps(event))),
                redirect_stdout(stdout),
                patch.dict(
                    "adamast.hosts.codex.dispatcher.HANDLERS",
                    {"SubagentStop": lambda _event, _config: None},
                ),
                patch("adamast.hosts.codex.dispatcher.poll_learning_jobs") as poll,
                patch("adamast.hosts.codex.dispatcher.reconcile_learning_jobs"),
                patch(
                    "adamast.hosts.codex.dispatcher.drain_learning_notices",
                    return_value=[],
                ) as drain,
                patch("adamast.hosts.codex.dispatcher.claim_learning_job") as claim,
            ):
                code = dispatcher_main(["--config", str(config_path)])

            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(), "")
            poll.assert_called_once()
            drain.assert_not_called()
            claim.assert_not_called()

    def test_dispatcher_keeps_learning_notices_queued_at_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(
                self.base_config(root),
                learning_backend="codex_subagent",
            )
            config_path = root / "adamast.json"
            config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            event = {
                "hook_event_name": "Stop",
                "session_id": "notice-after-stop",
                "cwd": str(root),
            }
            stdout = io.StringIO()

            with (
                patch("sys.stdin", io.StringIO(json.dumps(event))),
                redirect_stdout(stdout),
                patch.dict(
                    "adamast.hosts.codex.dispatcher.HANDLERS",
                    {"Stop": lambda _event, _config: None},
                ),
                patch("adamast.hosts.codex.dispatcher.poll_learning_jobs"),
                patch("adamast.hosts.codex.dispatcher.reconcile_learning_jobs"),
                patch(
                    "adamast.hosts.codex.dispatcher.drain_learning_notices",
                    return_value=["AdaMAST taxonomy generation triggered"],
                ) as drain,
            ):
                code = dispatcher_main(["--config", str(config_path)])

            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(), "")
            drain.assert_not_called()

    def test_dispatcher_keeps_learning_notices_queued_at_post_tool_use(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(
                self.base_config(root),
                learning_backend="codex_subagent",
            )
            config_path = root / "adamast.json"
            config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            event = {
                "hook_event_name": "PostToolUse",
                "session_id": "notice-after-tool",
                "cwd": str(root),
            }
            stdout = io.StringIO()

            with (
                patch("sys.stdin", io.StringIO(json.dumps(event))),
                redirect_stdout(stdout),
                patch.dict(
                    "adamast.hosts.codex.dispatcher.HANDLERS",
                    {"PostToolUse": lambda _event, _config: None},
                ),
                patch("adamast.hosts.codex.dispatcher.poll_learning_jobs"),
                patch("adamast.hosts.codex.dispatcher.reconcile_learning_jobs"),
                patch(
                    "adamast.hosts.codex.dispatcher.drain_learning_notices",
                    return_value=["AdaMAST taxonomy generation triggered"],
                ) as drain,
            ):
                code = dispatcher_main(["--config", str(config_path)])

            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(), "")
            drain.assert_not_called()

    def test_auto_project_scope_derives_program_from_event_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / "adamast-home"
            first = root / "first-project"
            second = root / "second-project"
            first.mkdir()
            second.mkdir()
            config = CodexConfig(
                trace_output=base,
                adamast_model="test-model",
                store_dir=STORE_DIR,
                dashboard=False,
                project_scope="auto",
                task_group="default",
            )
            first_config = config.for_event({"cwd": str(first)})
            second_config = config.for_event({"cwd": str(second)})
            self.assertNotEqual(
                first_config.trace_output,
                second_config.trace_output,
            )
            self.assertEqual(config.trace_output, base)
            self.assertIn("projects", first_config.trace_output.parts)
            self.assertEqual(first_config.trace_output.name, "program")

    def test_project_scope_round_trips_through_saved_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "adamast.json"
            config = CodexConfig(
                trace_output=root / "adamast-home",
                adamast_model="test-model",
                store_dir=STORE_DIR,
                dashboard=False,
                project_scope="auto",
                project_id="company-tools",
                task_group="platform",
            )
            path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            loaded = CodexConfig.load(path)
            self.assertEqual(loaded.project_scope, "auto")
            self.assertEqual(loaded.project_id, "company-tools")
            self.assertEqual(loaded.task_group, "platform")

    def test_session_selector_round_trips_through_saved_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "adamast.json"
            config = replace(
                self.base_config(root),
                session_selector="prompt",
                selector_surface="inline",
            )
            path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            loaded = CodexConfig.load(path)
            self.assertEqual(loaded.session_selector, "prompt")
            self.assertEqual(loaded.selector_surface, "inline")

    def test_native_learning_backend_round_trips_without_api_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "adamast.json"
            config = replace(
                self.base_config(root),
                learning_backend="codex_subagent",
                worker_model="gpt-test",
                codex_cli_path=root / "codex.exe",
                worker_timeout_seconds=321,
            )
            path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            loaded = CodexConfig.load(path)
            self.assertEqual(loaded.learning_backend, "codex_subagent")
            self.assertEqual(loaded.worker_model, "gpt-test")
            self.assertEqual(loaded.codex_cli_path, (root / "codex.exe").resolve())
            self.assertEqual(loaded.worker_timeout_seconds, 321)
            self.assertIsNone(loaded.openai_api_key_env)

    def test_native_learning_rejects_blocking_provider_modes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "requires background"):
                replace(
                    self.base_config(root),
                    learning_backend="codex_subagent",
                    generation_stops=True,
                )

    def test_install_writes_project_hooks_and_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(
                self.base_config(root),
                session_selector="prompt",
                selector_surface="browser",
            )
            result = install(root, config, python=Path("python"))
            hooks_path = Path(result["hooks"])
            config_path = Path(result["config"])
            self.assertTrue(hooks_path.exists())
            self.assertTrue(config_path.exists())
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]
            self.assertIn("SessionStart", hooks)
            self.assertIn("UserPromptSubmit", hooks)
            self.assertIn("Stop", hooks)
            text = json.dumps(hooks)
            self.assertIn("adamast.hosts.codex.dispatcher", text)
            status_messages = {
                event: entries[0]["hooks"][0]["statusMessage"]
                for event, entries in hooks.items()
            }
            self.assertEqual(
                status_messages,
                {
                    "SessionStart": "Restoring AdaMAST taxonomy",
                    "UserPromptSubmit": "Checking AdaMAST state",
                    "Stop": "Saving AdaMAST trace",
                    "SubagentStop": "Reconciling AdaMAST learning",
                    "PostToolUse": "Polling AdaMAST",
                },
            )
            timeouts = {
                event: entries[0]["hooks"][0]["timeout"]
                for event, entries in hooks.items()
            }
            self.assertEqual(timeouts["UserPromptSubmit"], 1815)
            self.assertTrue(
                all(
                    timeout == 30
                    for event, timeout in timeouts.items()
                    if event != "UserPromptSubmit"
                )
            )

    def test_user_level_install_is_zero_config_and_native_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch(
                    "adamast.hosts.codex.install.Path.home",
                    return_value=root,
                ),
                redirect_stdout(io.StringIO()) as output,
            ):
                code = install_main(["--user-level"])

            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["scope"], "user")
            config = CodexConfig.load(root / ".codex" / "adamast.json")
            self.assertEqual(
                config.trace_output.resolve(),
                (root / ".adamast" / "interactive").resolve(),
            )
            self.assertEqual(config.adamast_model, "interactive-session")
            self.assertEqual(config.project_scope, "auto")
            self.assertEqual(config.session_selector, "prompt")
            self.assertEqual(config.selector_surface, "browser")
            self.assertEqual(config.learning_backend, "codex_subagent")
            self.assertIsNone(config.openai_api_key_env)
            self.assertTrue(
                (root / ".agents" / "skills" / SKILL_NAME / "SKILL.md").is_file()
            )

    def test_install_main_preserves_configured_selector_surface(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "adamast.json"
            configured = replace(
                self.base_config(root),
                session_selector="prompt",
                selector_surface="inline",
            )
            config_path.write_text(
                json.dumps(configured.to_dict()),
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                code = install_main(
                    [
                        "--config",
                        str(config_path),
                        "--project-dir",
                        str(root),
                    ]
                )

            self.assertEqual(code, 0)
            installed = CodexConfig.load(root / ".codex" / "adamast.json")
            self.assertEqual(installed.selector_surface, "inline")

    def test_install_main_selector_surface_flag_overrides_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "adamast.json"
            configured = replace(
                self.base_config(root),
                session_selector="prompt",
                selector_surface="browser",
            )
            config_path.write_text(
                json.dumps(configured.to_dict()),
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                code = install_main(
                    [
                        "--config",
                        str(config_path),
                        "--project-dir",
                        str(root),
                        "--selector-surface",
                        "inline",
                    ]
                )

            self.assertEqual(code, 0)
            installed = CodexConfig.load(root / ".codex" / "adamast.json")
            self.assertEqual(installed.selector_surface, "inline")

    def test_user_level_install_rejects_project_target(self):
        with self.assertRaises(SystemExit):
            install_main(["--user-level", "--project-dir", "."])

    def test_uninstall_removes_only_adamast_hooks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install(root, self.base_config(root), python=Path("python"))
            hooks_path = root / ".codex" / "hooks.json"
            data = json.loads(hooks_path.read_text(encoding="utf-8"))
            data["hooks"].setdefault("Stop", []).append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python other.py",
                        }
                    ]
                }
            )
            hooks_path.write_text(json.dumps(data), encoding="utf-8")
            result = uninstall(root)
            self.assertGreater(result["removed_hooks"], 0)
            cleaned = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertIn("other.py", json.dumps(cleaned))
            self.assertFalse((root / ".codex" / "adamast.json").exists())

    def test_uninstall_preserves_unrelated_config_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install(root, self.base_config(root), python=Path("python"))
            hooks_path = root / ".codex" / "hooks.json"
            data = json.loads(hooks_path.read_text(encoding="utf-8"))
            data["hooks"].setdefault("Stop", []).append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python backups/adamast.json",
                        }
                    ]
                }
            )
            hooks_path.write_text(json.dumps(data), encoding="utf-8")

            uninstall(root)

            cleaned = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertIn("backups/adamast.json", json.dumps(cleaned))

    def test_stop_hook_ignores_visible_compact_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.base_config(root)
            event = {
                "hook_event_name": "SessionStart",
                "session_id": "codex-session",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            start = session_start(event, config)
            self.assertIn(
                "AdaMAST runtime interaction is active",
                start["hookSpecificOutput"]["additionalContext"],
            )
            append_text(transcript, "Compute 2 + 2.", role="user")
            final_report = (
                "2 + 2 = 4. I am ready to submit.\n\n"
                + compact_report(
                    "arithmetic verified",
                    evidence="2 + 2 = 4",
                )
            )
            append_text(transcript, final_report)
            accepted = stop(
                {**event, "hook_event_name": "Stop"},
                config,
            )
            self.assertTrue(accepted["continue"])
            self.assertNotIn("decision", accepted)
            self.assertIn("private final checkpoint", accepted["systemMessage"])
            state = load_state(config.trace_output, "codex-session")
            self.assertTrue(state["trace_captured"])
            self.assertEqual(
                state["gate_result"]["status"],
                "MISSING_PRIVATE_CHECKPOINT",
            )
            self.assertFalse((config.trace_output / EVIDENCE_FILE).exists())
            self.assertEqual(state["taxonomy_id"], "mast")

    def test_direct_checkpoint_is_recorded_without_chat_block_and_enriched_at_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.base_config(root)
            event = {
                "session_id": "direct-checkpoint",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            started = session_start(
                {**event, "hook_event_name": "SessionStart"},
                config,
            )
            context = started["hookSpecificOutput"]["additionalContext"]
            self.assertIn("adamast-codex-checkpoint", context)
            self.assertIn("do not print", context.lower())
            append_text(transcript, "Verify the direct recorder.", role="user")

            recorded = record_checkpoint(
                config.trace_output,
                event["session_id"],
                compact_report(
                    "DIRECT_GATE_READY",
                    evidence="the direct recorder accepted the gate",
                ),
            )
            self.assertTrue(recorded["recorded"])
            evidence_path = config.trace_output / EVIDENCE_FILE
            before_stop = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(len(before_stop["checkpoints"]), 1)
            self.assertEqual(before_stop["checkpoints"][0]["source"], "codex_direct")
            self.assertIsNone(before_stop["checkpoints"][0]["turn_id"])

            final_answer = "The direct recorder is verified."
            append_text(transcript, final_answer)
            accepted = stop(
                {
                    **event,
                    "hook_event_name": "Stop",
                    "last_assistant_message": final_answer,
                    "turn_id": "turn-exact-42",
                },
                config,
            )

            self.assertEqual(accepted, {"continue": True})
            after_stop = json.loads(evidence_path.read_text(encoding="utf-8"))
            checkpoint = after_stop["checkpoints"][0]
            self.assertEqual(checkpoint["turn_id"], "turn-exact-42")
            self.assertEqual(checkpoint["episode_sequence"], 1)
            state = load_state(config.trace_output, event["session_id"])
            self.assertEqual(state["gate_result"]["status"], "READY_TO_SUBMIT")
            self.assertEqual(
                state["gate_result"]["checkpoint_id"],
                recorded["checkpoint_id"],
            )

    def test_direct_checkpoint_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.base_config(root)
            event = {
                "session_id": "direct-checkpoint-retry",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)
            report = compact_report("IDEMPOTENT_GATE")
            first = record_checkpoint(config.trace_output, event["session_id"], report)
            second = record_checkpoint(config.trace_output, event["session_id"], report)

            self.assertTrue(first["recorded"])
            self.assertTrue(second["duplicate"])
            self.assertEqual(first["checkpoint_id"], second["checkpoint_id"])
            evidence = json.loads(
                (config.trace_output / EVIDENCE_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(len(evidence["checkpoints"]), 1)

    def test_multiple_checkpoints_share_one_codex_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.base_config(root)
            event = {
                "session_id": "multi-checkpoint-turn",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)
            first = record_checkpoint(
                config.trace_output,
                event["session_id"],
                compact_report("TOOL_FAILURE_GATE", next_action="repair the tool call"),
                gate="tool_failure",
            )
            final = record_checkpoint(
                config.trace_output,
                event["session_id"],
                compact_report("FINAL_GATE"),
                gate="stop",
            )

            stop(
                {
                    **event,
                    "hook_event_name": "Stop",
                    "turn_id": "turn-with-two-gates",
                },
                config,
            )

            evidence = json.loads(
                (config.trace_output / EVIDENCE_FILE).read_text(encoding="utf-8")
            )
            checkpoints = evidence["checkpoints"]
            self.assertEqual(len(checkpoints), 2)
            self.assertEqual(
                {item["checkpoint_id"] for item in checkpoints},
                {first["checkpoint_id"], final["checkpoint_id"]},
            )
            self.assertEqual(
                {item["turn_id"] for item in checkpoints},
                {"turn-with-two-gates"},
            )
            self.assertEqual(
                {item["gate"] for item in checkpoints},
                {"tool_failure", "stop"},
            )

    def test_codex_monitor_opens_once_for_a_new_conversation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = replace(self.base_config(root), dashboard=True)
            event = {
                "session_id": "monitor-autostart",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            monitor_url = (
                "http://127.0.0.1:8765/"
                "?project=demo&conversation=monitor-autostart"
            )

            with (
                patch(
                    "adamast.dashboard.server.ensure_dashboard",
                    return_value=monitor_url,
                ) as ensure,
                patch(
                    "adamast.hosts.codex.runtime.webbrowser.open",
                    return_value=True,
                ) as open_browser,
            ):
                started = session_start(
                    {**event, "hook_event_name": "SessionStart"}, config
                )

            ensure.assert_called_once()
            open_browser.assert_called_once_with(monitor_url)
            context = started["hookSpecificOutput"]["additionalContext"]
            self.assertIn(monitor_url, context)
            state = load_state(config.trace_output, event["session_id"])
            self.assertTrue(state["monitor_opened"])
            self.assertEqual(state["dashboard_url"], monitor_url)

    def test_successive_completed_turns_become_distinct_episode_traces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.base_config(root)
            event = {
                "session_id": "multi-turn",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)

            def complete_turn(task: str, answer: str) -> None:
                append_text(transcript, task, role="user")
                report = answer + "\n\n" + compact_report(task)
                append_text(transcript, report)
                accepted = stop(
                    {
                        **event,
                        "hook_event_name": "Stop",
                        "last_assistant_message": report,
                    },
                    config,
                )
                self.assertTrue(accepted["continue"])

            complete_turn("FIRST TASK MARKER", "FIRST ANSWER MARKER")
            complete_turn("SECOND TASK MARKER", "SECOND ANSWER MARKER")
            duplicate = stop({**event, "hook_event_name": "Stop"}, config)
            self.assertTrue(duplicate["continue"])
            self.assertIn("already committed", duplicate["systemMessage"])

            traces = sorted(
                ProgramWorkspace(config.trace_output).pending.iter_traces(),
                key=lambda trace: trace.metadata["episode_sequence"],
            )
            self.assertEqual(len(traces), 2)
            self.assertEqual(
                [trace.metadata["episode_sequence"] for trace in traces],
                [1, 2],
            )
            self.assertEqual(traces[0].task, "FIRST TASK MARKER")
            self.assertEqual(traces[1].task, "SECOND TASK MARKER")
            self.assertIn("FIRST ANSWER MARKER", traces[0].raw_trajectory)
            self.assertNotIn("FIRST ANSWER MARKER", traces[1].raw_trajectory)
            self.assertIn("SECOND ANSWER MARKER", traces[1].raw_trajectory)
            state = load_state(config.trace_output, "multi-turn")
            self.assertEqual(state["episode_sequence"], 2)
            self.assertEqual(state["conversation_taxonomy_root"], "mast")

    def test_missing_compact_checkpoint_warns_but_never_strands_episode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.base_config(root)
            event = {
                "session_id": "missing-checkpoint",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)
            append_text(transcript, "Complete the task.", role="user")
            append_text(transcript, "The task is complete and verified.")

            result = stop({**event, "hook_event_name": "Stop"}, config)

            self.assertTrue(result["continue"])
            self.assertIn("private final checkpoint", result["systemMessage"])
            state = load_state(config.trace_output, event["session_id"])
            self.assertTrue(state["finished"])
            self.assertTrue(state["trace_captured"])
            self.assertEqual(
                state["gate_result"]["status"],
                "MISSING_PRIVATE_CHECKPOINT",
            )

    def test_subagent_stop_is_observational_and_never_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.base_config(root)
            event = {
                "session_id": "subagent-observational",
                "cwd": str(root),
                "transcript_path": str(transcript),
                "agent_id": "worker-1",
                "agent_type": "worker",
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)

            plain = subagent_stop(
                {
                    **event,
                    "hook_event_name": "SubagentStop",
                    "last_assistant_message": "Worker finished without a checkpoint.",
                },
                config,
            )
            self.assertIsNone(plain)

            checkpoint = compact_report("worker pass complete")
            captured = subagent_stop(
                {
                    **event,
                    "hook_event_name": "SubagentStop",
                    "last_assistant_message": checkpoint,
                },
                config,
            )
            self.assertIsNone(captured)
            self.assertFalse((config.trace_output / EVIDENCE_FILE).exists())

    def test_next_user_prompt_recovers_skipped_stop_without_merging_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.base_config(root)
            event = {
                "session_id": "recover-next-prompt",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)
            append_text(transcript, "FIRST RECOVERY TASK", role="user")
            user_prompt_submit(
                {
                    **event,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "FIRST RECOVERY TASK",
                },
                config,
            )
            append_text(transcript, "FIRST RECOVERY ANSWER")

            append_text(transcript, "SECOND RECOVERY TASK", role="user")
            recovered = user_prompt_submit(
                {
                    **event,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "SECOND RECOVERY TASK",
                },
                config,
            )

            self.assertIn("recovered", recovered["systemMessage"].lower())
            traces = list(ProgramWorkspace(config.trace_output).pending.iter_traces())
            self.assertEqual(len(traces), 1)
            self.assertEqual(traces[0].task, "FIRST RECOVERY TASK")
            self.assertIn("FIRST RECOVERY ANSWER", traces[0].raw_trajectory)
            self.assertNotIn("SECOND RECOVERY TASK", traces[0].raw_trajectory)
            state = load_state(config.trace_output, event["session_id"])
            self.assertFalse(state["finished"])
            self.assertEqual(state["episode_sequence"], 2)
            self.assertEqual(state["episode_task"], "SECOND RECOVERY TASK")

    def test_codex_transcript_normalizer_excludes_harness_context(self):
        with tempfile.TemporaryDirectory() as temp:
            transcript = Path(temp) / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            append_item(
                transcript,
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "SECRET SYSTEM"}],
                    },
                },
            )
            append_item(
                transcript,
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "call_id": "skill-read",
                        "name": "exec",
                        "input": (
                            "Get-Content C:\\Users\\tester\\.codex\\skills\\"
                            "browser\\SKILL.md"
                        ),
                    },
                },
            )
            append_item(
                transcript,
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call_output",
                        "call_id": "skill-read",
                        "output": "Control the in-app Browser. PRIVATE SKILL TEXT",
                    },
                },
            )
            append_item(
                transcript,
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "REAL HUMAN TASK",
                    },
                },
            )
            append_item(
                transcript,
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "REAL HUMAN TASK"}],
                    },
                },
            )
            append_item(
                transcript,
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "<hook_prompt>AdaMAST PRIVATE TAXONOMY</hook_prompt>",
                            }
                        ],
                    },
                },
            )
            append_item(
                transcript,
                {
                    "type": "event_msg",
                    "payload": {"type": "agent_reasoning", "text": "PRIVATE REASONING"},
                },
            )
            append_item(
                transcript,
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "call_id": "call-1",
                        "name": "exec",
                        "input": '{"command":"pytest"}',
                    },
                },
            )
            append_item(
                transcript,
                {
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "VISIBLE ANSWER"},
                },
            )

            normalized = read_raw_transcript(transcript)

            self.assertEqual(first_user_message(transcript), "REAL HUMAN TASK")
            self.assertEqual(normalized.count("REAL HUMAN TASK"), 1)
            self.assertIn("VISIBLE ANSWER", normalized)
            self.assertIn("pytest", normalized)
            self.assertNotIn("SECRET SYSTEM", normalized)
            self.assertNotIn("AdaMAST PRIVATE TAXONOMY", normalized)
            self.assertNotIn("PRIVATE REASONING", normalized)
            self.assertNotIn("PRIVATE SKILL TEXT", normalized)

    def test_external_tool_workdir_surfaces_project_scope_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bound = root / "bound"
            external = root / "external"
            bound.mkdir()
            external.mkdir()
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.base_config(root)
            event = {
                "session_id": "scope-warning",
                "cwd": str(bound),
                "transcript_path": str(transcript),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)
            append_text(transcript, "Work on the bound project.", role="user")
            append_item(
                transcript,
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "call_id": "scope-call",
                        "name": "exec",
                        "input": (
                            'tools.shell_command({command:"git status",'
                            f'"workdir":"{str(external).replace(chr(92), chr(92) * 2)}"'
                            "})"
                        ),
                    },
                },
            )
            report = "Done.\n\n" + compact_report("scope task complete")
            append_text(transcript, report)

            result = stop(
                {**event, "hook_event_name": "Stop", "last_assistant_message": report},
                config,
            )

            self.assertIn("project scope mismatch", result["systemMessage"].lower())
            trace = next(ProgramWorkspace(config.trace_output).pending.iter_traces())
            self.assertEqual(trace.metadata["external_workdirs"], [str(external.resolve())])

    def test_five_completed_episodes_queue_exactly_one_generation_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = replace(
                self.base_config(root),
                learning_backend="codex_subagent",
                generation_threshold=5,
                codex_cli_path=Path(sys.executable),
            )
            event = {
                "session_id": "five-episode-generation",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)
            workspace = ProgramWorkspace(config.trace_output)
            with workspace.locked_manifest() as manifest:
                manifest["generation"]["retry_after_count"] = 5

            with (
                patch(
                    "adamast.hosts.codex.runtime.enqueue_learning_job",
                    return_value="codex-generation-five",
                ) as enqueue,
                patch(
                    "adamast.learning.generation._adamast_generate",
                    side_effect=AssertionError("provider generation must not run"),
                ) as provider,
            ):
                for index in range(1, 6):
                    append_text(transcript, f"TASK {index}", role="user")
                    report = (
                        f"ANSWER {index}\n\n"
                        + compact_report(f"task {index} complete")
                    )
                    append_text(transcript, report)
                    result = stop(
                        {
                            **event,
                            "hook_event_name": "Stop",
                            "last_assistant_message": report,
                        },
                        config,
                    )
                    self.assertTrue(result["continue"])

            self.assertEqual(enqueue.call_count, 1)
            self.assertEqual(enqueue.call_args.kwargs["kind"], "generation")
            provider.assert_not_called()
            traces = list(workspace.pending.iter_traces())
            self.assertEqual(len(traces), 5)

    def test_native_backend_queues_codex_worker_without_provider_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = replace(
                self.base_config(root),
                learning_backend="codex_subagent",
                generation_threshold=1,
                codex_cli_path=Path(sys.executable),
            )
            event = {
                "session_id": "native-learning",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)
            workspace = ProgramWorkspace(config.trace_output)
            with workspace.locked_manifest() as manifest:
                manifest["generation"]["retry_after_count"] = 1
            append_text(transcript, "Build the demo.", role="user")
            report = (
                "The demo is built and verified.\n\n"
                + compact_report("demo built")
            )
            append_text(transcript, report)
            with (
                patch(
                    "adamast.hosts.codex.runtime.enqueue_learning_job",
                    return_value="codex-generation-test",
                ) as enqueue,
                patch(
                    "adamast.learning.generation._adamast_generate",
                    side_effect=AssertionError("provider generation must not run"),
                ) as provider,
            ):
                accepted = stop(
                    {
                        **event,
                        "hook_event_name": "Stop",
                        "last_assistant_message": report,
                    },
                    config,
            )
            self.assertTrue(accepted["continue"])
            native_state = load_state(config.trace_output, "native-learning")
            self.assertEqual(
                enqueue.call_count,
                1,
                msg=f"trace capture: {native_state.get('trace_capture')}",
            )
            self.assertEqual(enqueue.call_args.kwargs["kind"], "generation")
            provider.assert_not_called()

    def test_selector_holds_first_task_then_resumes_with_mast(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            empty_store = root / "taxonomies"
            empty_store.mkdir()
            config = self.selector_config(root, store_dir=empty_store)
            event = {
                "session_id": "selector-mast",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }

            started = session_start(
                {**event, "hook_event_name": "SessionStart"}, config
            )
            context = started["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Which taxonomy should AdaMAST use", context)
            self.assertIn("1. MAST  [Recommended]", context)
            self.assertIn("2. No taxonomy", context)

            append_text(transcript, "ORIGINAL TASK MARKER", role="user")
            held = user_prompt_submit(
                {
                    **event,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "ORIGINAL TASK MARKER",
                },
                config,
            )
            self.assertIn(
                "Do not perform or analyze",
                held["hookSpecificOutput"]["additionalContext"],
            )
            append_text(transcript, "AdaMAST SELECTOR MARKER")
            waiting = stop({**event, "hook_event_name": "Stop"}, config)
            self.assertTrue(waiting["continue"])
            self.assertIn("waiting for taxonomy", waiting["systemMessage"])

            append_text(transcript, "1", role="user")
            accepted_choice = user_prompt_submit(
                {
                    **event,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "1",
                },
                config,
            )
            self.assertIn("Continue it now", json.dumps(accepted_choice))
            state = load_state(config.trace_output, "selector-mast")
            self.assertEqual(state["selection"]["status"], "selected")
            self.assertEqual(state["taxonomy_id"], "mast")
            self.assertEqual(state["episode_task"], "ORIGINAL TASK MARKER")

            append_text(transcript, "ORIGINAL TASK ANSWER MARKER")
            report = (
                "ORIGINAL TASK ANSWER MARKER\n\n"
                + compact_report("original task complete")
            )
            append_text(transcript, report)
            final = stop(
                {
                    **event,
                    "hook_event_name": "Stop",
                    "last_assistant_message": report,
                },
                config,
            )
            self.assertTrue(final["continue"])

            traces = list(ProgramWorkspace(config.trace_output).pending.iter_traces())
            self.assertEqual(len(traces), 1)
            self.assertEqual(traces[0].task, "ORIGINAL TASK MARKER")
            self.assertIn("ORIGINAL TASK ANSWER MARKER", traces[0].raw_trajectory)
            self.assertNotIn("AdaMAST SELECTOR MARKER", traces[0].raw_trajectory)

    def test_resume_recovers_missed_inline_choice_before_browser_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.selector_config(root)
            event = {
                "session_id": "selector-resume-recovery",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            session_start(
                {**event, "hook_event_name": "SessionStart"},
                config,
            )
            append_text(transcript, "Inspect the existing experiment.", role="user")
            append_text(transcript, "MAST", role="user")

            browser_config = replace(config, selector_surface="browser")
            with patch(
                "adamast.hosts.codex.runtime.start_browser_picker"
            ) as launch:
                resumed = session_start(
                    {**event, "hook_event_name": "SessionStart"},
                    browser_config,
                )

            launch.assert_not_called()
            state = load_state(config.trace_output, event["session_id"])
            self.assertEqual(state["selection"]["status"], "selected")
            self.assertEqual(
                state["selection"]["selected_taxonomy_id"],
                "mast",
            )
            self.assertEqual(
                state["selector_recovery"]["source"],
                "transcript",
            )
            self.assertIn(
                "taxonomy is pinned to MAST",
                resumed["hookSpecificOutput"]["additionalContext"],
            )
            self.assertIn("recovered", resumed["systemMessage"].lower())

    def test_browser_catalog_waits_for_user_prompt_and_applies_directly(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = replace(
                self.selector_config(root),
                selector_surface="browser",
                worker_timeout_seconds=10,
            )
            event = {
                "session_id": "selector-browser",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }

            result: dict[str, object] = {}

            def submit() -> None:
                result["output"] = user_prompt_submit(
                    {
                        **event,
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": "Inspect the project.",
                    },
                    config,
                )

            with patch(
                "adamast.hosts.codex.runtime.open_browser_picker",
                return_value=True,
            ):
                started = session_start(
                    {**event, "hook_event_name": "SessionStart"}, config
                )
                self.assertIsNone(started)
                self.assertFalse(config.trace_output.exists())
                thread = threading.Thread(target=submit, daemon=True)
                thread.start()
                picker = None
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    waiting = load_state(config.trace_output, event["session_id"])
                    picker = (waiting.get("selection") or {}).get("browser_picker")
                    if picker:
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(picker)
                self.assertTrue(thread.is_alive(), "prompt hook returned before choice")
                with urlopen(
                    picker["url"] + "choose?id=tax-django-orm-001",
                    timeout=5,
                ) as response:
                    response.read()
                thread.join(timeout=10)

            self.assertFalse(thread.is_alive(), "prompt hook did not resume after choice")
            output = result["output"]
            self.assertNotIn("continue", output)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("submitted prompt is the held task", context)
            self.assertNotIn("Inspect the project.", context)
            state = load_state(config.trace_output, event["session_id"])
            self.assertEqual(state["selection"]["status"], "selected")
            self.assertEqual(
                state["selection"]["selected_taxonomy_id"],
                "tax-django-orm-001",
            )
            self.assertEqual(state["episode_task"], "Inspect the project.")
            self.assertFalse(state["finished"])

    def test_browser_selection_timeout_stops_original_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = replace(
                self.selector_config(root),
                selector_surface="browser",
                worker_timeout_seconds=1,
            )
            event = {
                "session_id": "selector-browser-timeout",
                "cwd": str(root),
                "transcript_path": str(transcript),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Do not run this task after timeout.",
            }
            picker = {
                "url": "http://127.0.0.1:9/",
                "result_path": str(root / "missing-choice.json"),
            }
            with (
                patch(
                    "adamast.hosts.codex.runtime.start_browser_picker",
                    return_value=picker,
                ),
                patch(
                    "adamast.hosts.codex.runtime.open_browser_picker",
                    return_value=True,
                ),
                patch(
                    "adamast.hosts.codex.runtime.wait_for_browser_choice",
                    return_value=None,
                ),
            ):
                output = user_prompt_submit(event, config)

            self.assertFalse(output["continue"])
            self.assertIn("timed out", output["stopReason"])
            self.assertNotIn(
                event["prompt"],
                output["hookSpecificOutput"]["additionalContext"],
            )

    def test_legacy_pending_selector_refreshes_before_parsing_old_number(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = self.selector_config(root)
            event = {
                "session_id": "selector-refresh",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)
            state = load_state(config.trace_output, event["session_id"])
            state["selection"].pop("version", None)
            state["selection"]["options"] = [
                state["selection"]["options"][0],
                *state["selection"]["catalog_options"],
                state["selection"]["options"][-1],
            ]
            save_state(config.trace_output, event["session_id"], state)

            refreshed = user_prompt_submit(
                {
                    **event,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "2",
                },
                config,
            )
            context = refreshed["hookSpecificOutput"]["additionalContext"]
            self.assertIn("2. web-backend", context)
            self.assertIn("8. No taxonomy", context)
            updated = load_state(config.trace_output, event["session_id"])
            self.assertEqual(updated["selection"]["status"], "pending")

    def test_no_taxonomy_disables_gates_and_trace_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            empty_store = root / "taxonomies"
            empty_store.mkdir()
            config = self.selector_config(root, store_dir=empty_store)
            event = {
                "session_id": "selector-disabled",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            session_start({**event, "hook_event_name": "SessionStart"}, config)
            append_text(transcript, "DISABLED TASK", role="user")
            user_prompt_submit(
                {
                    **event,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "DISABLED TASK",
                },
                config,
            )
            append_text(transcript, "No taxonomy", role="user")
            selected = user_prompt_submit(
                {
                    **event,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "No taxonomy",
                },
                config,
            )
            self.assertIn("AdaMAST disabled", selected["systemMessage"])
            self.assertIsNone(stop({**event, "hook_event_name": "Stop"}, config))
            state = load_state(config.trace_output, "selector-disabled")
            self.assertEqual(state["selection"]["status"], "disabled")
            workspace = ProgramWorkspace(config.trace_output)
            self.assertEqual(workspace.load()["active_sessions"], [])
            self.assertEqual(list(workspace.pending.iter_traces()), [])

    def test_stored_taxonomy_seeds_only_one_conversation_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            base_config = self.selector_config(root)
            first = {
                "session_id": "selector-taxonomy-first",
                "cwd": str(root),
                "transcript_path": str(transcript),
            }
            first_config = base_config.for_event(first)
            session_start({**first, "hook_event_name": "SessionStart"}, first_config)
            user_prompt_submit(
                {
                    **first,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "tax-django-orm-001",
                },
                first_config,
            )
            user_prompt_submit(
                {
                    **first,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "Inspect the ORM.",
                },
                first_config,
            )
            self.assertEqual(
                ProgramWorkspace(first_config.trace_output).load()["taxonomy_id"],
                "tax-django-orm-001",
            )

            second = {**first, "session_id": "selector-taxonomy-second"}
            second_config = base_config.for_event(second)
            self.assertNotEqual(first_config.trace_output, second_config.trace_output)
            second_manifest = ProgramWorkspace(second_config.trace_output).load()
            self.assertIsNone(second_manifest["taxonomy_id"])
            self.assertNotEqual(
                second_manifest["branch"]["branch_id"],
                ProgramWorkspace(first_config.trace_output).load()["branch"]["branch_id"],
            )
            shown = session_start(
                {**second, "hook_event_name": "SessionStart"},
                second_config,
            )
            context = shown["hookSpecificOutput"]["additionalContext"]
            self.assertIn("MAST  [Recommended]", context)
            self.assertIn("web-backend", context)
            self.assertIn("isolated branch", context)

    def test_simultaneous_mast_conversations_use_distinct_branch_programs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "codex.jsonl"
            transcript.write_text("", encoding="utf-8")
            base_config = replace(
                self.selector_config(root),
                trace_output=root / "adamast-home",
                project_scope="auto",
            )
            events = [
                {
                    "session_id": f"mast-conversation-{index}",
                    "cwd": str(root),
                    "transcript_path": str(transcript),
                }
                for index in (1, 2)
            ]
            configs = [base_config.for_event(event) for event in events]
            self.assertNotEqual(configs[0].trace_output, configs[1].trace_output)
            for event, config in zip(events, configs, strict=True):
                session_start({**event, "hook_event_name": "SessionStart"}, config)
                user_prompt_submit(
                    {
                        **event,
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": "MAST",
                    },
                    config,
                )
                manifest = ProgramWorkspace(config.trace_output).load()
                self.assertEqual(manifest["branch"]["conversation_id"], event["session_id"])
                self.assertIsNone(manifest["taxonomy_id"])
            self.assertNotEqual(configs[0].task_group, configs[1].task_group)

    def test_optional_skill_install_uninstall_still_works(self):
        with tempfile.TemporaryDirectory() as temp:
            skills_dir = Path(temp) / "skills"
            result = install_skill(skills_dir=skills_dir)
            self.assertEqual(result.skill_dir, skills_dir / SKILL_NAME)
            self.assertTrue(result.skill_md.exists())
            summary = uninstall_skill(skills_dir=skills_dir)
            self.assertIn(str(result.skill_md), summary["removed"])
            self.assertFalse(result.skill_md.exists())

    def test_skill_reinstall_updates_an_adamast_managed_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            skills_dir = Path(temp) / "skills"
            first = install_skill(skills_dir=skills_dir)
            first.skill_md.write_text("outdated managed skill", encoding="utf-8")

            second = install_skill(skills_dir=skills_dir)

            self.assertEqual(second.skill_dir, first.skill_dir)
            self.assertNotEqual(
                second.skill_md.read_text(encoding="utf-8"),
                "outdated managed skill",
            )

    def test_skill_install_refuses_an_unmanaged_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            skills_dir = Path(temp) / "skills"
            skill_dir = skills_dir / SKILL_NAME
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("user skill", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                install_skill(skills_dir=skills_dir)

    def test_skill_uninstall_refuses_unmarked_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            skills_dir = Path(temp) / "skills"
            skill_dir = skills_dir / SKILL_NAME
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("user skill", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                uninstall_skill(skills_dir=skills_dir)


if __name__ == "__main__":
    unittest.main()
