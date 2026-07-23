from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from urllib.request import urlopen

from adamast.core import store

from adamast.hosts.claude_code.config import ClaudeCodeConfig
from adamast.hosts.claude_code.dispatcher import (
    _merge_notices,
    main as dispatcher_main,
)
from adamast.hosts.claude_code.install import install
from adamast.hosts.claude_code.learning_jobs import (
    claim_learning_job,
    drain_learning_notices,
    enqueue_claude_learning_job,
    poll_learning_jobs,
    reconcile_learning_jobs,
)
from adamast.hosts.claude_code.native_worker import run_worker
from adamast.hosts.claude_code.runtime import session_start, user_prompt_submit
from adamast.hosts.claude_code.state import load_state, save_state
from adamast.hosts.claude_code.subagent_protocol import (
    RECEIPT_CLOSE,
    RECEIPT_OPEN,
    complete_support_review,
)
from adamast.hosts.claude_code.browser_picker import (
    apply_browser_choice,
    start_browser_picker,
)
from adamast.hosts.claude_code.hooks import subagent_stop
from adamast.hosts.claude_code.uninstall import uninstall
from adamast.hosts.codex.config import CodexConfig
from adamast.hosts.codex.subagent_protocol import (
    claim_learning_job as claim_codex_learning_job,
)
from adamast import GenerationTrace, ProgramWorkspace
from adamast.core.project_scope import project_program_path
from adamast.hosts.interactive.selector import build_selection


ROOT = Path(__file__).resolve().parent.parent
STORE_DIR = ROOT / "tests" / "fixtures" / "taxonomies"


class ClaudeNativeLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base_program = self.root / "adamast-home"
        self.project = self.root / "project"
        self.project.mkdir()
        self.transcript = self.root / "transcript.jsonl"
        self.transcript.write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def config(self, **changes) -> ClaudeCodeConfig:
        fields = {
            "trace_output": self.base_program,
            "adamast_model": "test-model",
            "store_dir": STORE_DIR,
            "trace_root": self.root / "traces",
            "dashboard": False,
            "session_selector": "prompt",
            "learning_backend": "claude_subagent",
            "claude_cli_path": Path(sys.executable),
        }
        fields.update(changes)
        return ClaudeCodeConfig(**fields)

    def event(self, name: str, **changes) -> dict:
        value = {
            "hook_event_name": name,
            "session_id": "claude-session-1",
            "cwd": str(self.project),
            "transcript_path": str(self.transcript),
        }
        value.update(changes)
        return value

    def test_selector_holds_task_then_starts_mast_episode(self) -> None:
        config = self.config()
        output = session_start(self.event("SessionStart"), config)
        self.assertIn("Which taxonomy", output["systemMessage"])
        self.assertIn("No taxonomy", output["systemMessage"])

        blocked = user_prompt_submit(
            self.event("UserPromptSubmit", prompt="Build the company tools demo"),
            config,
        )
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("Which taxonomy", blocked["reason"])
        state = load_state(config.trace_output, "claude-session-1")
        self.assertEqual(
            state["selection"]["pending_task"],
            "Build the company tools demo",
        )

        accepted = user_prompt_submit(
            self.event("UserPromptSubmit", prompt="MAST"),
            config,
        )
        self.assertIn("AdaMAST selected MAST", accepted["systemMessage"])
        self.assertIn(
            "Build the company tools demo",
            accepted["hookSpecificOutput"]["additionalContext"],
        )
        state = load_state(config.trace_output, "claude-session-1")
        self.assertEqual(state["selection"]["status"], "selected")
        self.assertEqual(state["episode_task"], "Build the company tools demo")
        self.assertFalse(state["finished"])

    def test_browser_selection_resumes_original_prompt_without_reprompt(self) -> None:
        config = self.config(
            selector_surface="browser",
            worker_timeout_seconds=10,
        )
        session_start(self.event("SessionStart"), config)
        original_prompt = "Run the smoke test and summarize it."
        result: dict[str, object] = {}

        def submit() -> None:
            result["output"] = user_prompt_submit(
                self.event("UserPromptSubmit", prompt=original_prompt),
                config,
            )

        with patch(
            "adamast.hosts.claude_code.runtime.open_browser_picker",
            return_value=True,
        ):
            thread = threading.Thread(target=submit, daemon=True)
            thread.start()
            picker = None
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                state = load_state(config.trace_output, "claude-session-1")
                picker = (state.get("selection") or {}).get("browser_picker")
                if picker:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(picker)
            self.assertTrue(thread.is_alive(), "prompt hook returned before choice")
            with urlopen(picker["url"] + "choose?id=mast", timeout=5) as response:
                response.read()
            thread.join(timeout=10)

        self.assertFalse(thread.is_alive(), "prompt hook did not resume after choice")
        output = result["output"]
        self.assertNotIn("decision", output)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("submitted prompt is the held task", context)
        self.assertNotIn(original_prompt, context)
        state = load_state(config.trace_output, "claude-session-1")
        self.assertEqual(state["selection"]["status"], "selected")
        self.assertEqual(state["episode_task"], original_prompt)
        self.assertFalse(state["finished"])

    def test_browser_selection_timeout_blocks_without_echoing_prompt(self) -> None:
        config = self.config(selector_surface="browser", worker_timeout_seconds=1)
        session_start(self.event("SessionStart"), config)
        picker = {
            "url": "http://127.0.0.1:9/",
            "result_path": str(self.root / "missing-choice.json"),
        }
        with (
            patch(
                "adamast.hosts.claude_code.runtime.start_browser_picker",
                return_value=picker,
            ),
            patch(
                "adamast.hosts.claude_code.runtime.open_browser_picker",
                return_value=True,
            ),
            patch(
                "adamast.hosts.claude_code.runtime.wait_for_browser_choice",
                return_value=None,
            ),
        ):
            output = user_prompt_submit(
                self.event("UserPromptSubmit", prompt="Do not echo this prompt"),
                config,
            )

        self.assertEqual(output["decision"], "block")
        self.assertTrue(output["suppressOriginalPrompt"])
        self.assertIn("timed out", output["reason"])

    def test_resume_recovers_missed_inline_choice_before_browser_launch(self) -> None:
        inline_config = self.config(selector_surface="inline")
        session_start(self.event("SessionStart"), inline_config)
        with self.transcript.open("a", encoding="utf-8") as handle:
            for text in ("Inspect the existing experiment.", "MAST"):
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

        browser_config = self.config(selector_surface="browser")
        with patch(
            "adamast.hosts.claude_code.runtime.start_browser_picker"
        ) as launch:
            resumed = session_start(self.event("SessionStart"), browser_config)

        launch.assert_not_called()
        state = load_state(browser_config.trace_output, "claude-session-1")
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

    def test_codex_and_claude_auto_scope_use_distinct_host_programs(self) -> None:
        claude = self.config(project_scope="auto", task_group="platform")
        codex = CodexConfig(
            trace_output=self.base_program,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            trace_root=self.root / "traces",
            dashboard=False,
            project_scope="auto",
            task_group="platform",
        )
        event = {"cwd": str(self.project)}
        claude_path = claude.for_event(event).trace_output
        codex_path = codex.for_event(event).trace_output
        self.assertNotEqual(claude_path, codex_path)
        self.assertEqual(claude_path.parent.name, "claude-code-platform")
        self.assertEqual(codex_path.parent.name, "codex-platform")

    def test_codex_cannot_claim_a_claude_native_job(self) -> None:
        program = self.root / "claude-owned-program"
        workspace = ProgramWorkspace(program, repo="company-tools")
        with workspace.locked_manifest() as manifest:
            manifest["host"] = "claude_code"
            manifest["source"] = {
                "host": "Claude Code",
                "host_id": "claude_code",
                "project": "company-tools",
                "conversation_id": "claude-owner",
                "conversation_name": "Claude owner",
            }
        workspace.pending.append_many_with_names(
            GenerationTrace(
                problem_id=f"episode-{index}",
                task=f"Task {index}",
                raw_trajectory=f"Failure evidence {index}",
            )
            for index in range(1, 6)
        )
        job_id = enqueue_claude_learning_job(
            workspace,
            kind="generation",
            store_dir=self.root / "claude-taxonomies",
            trace_root=self.root / "claude-traces",
            task_group="claude-code-default",
            conversation_id="claude-owner",
        )

        self.assertIsNone(
            claim_codex_learning_job(
                workspace,
                conversation_id="codex-intruder",
            )
        )
        claimed = claim_learning_job(
            workspace,
            conversation_id="claude-owner",
        )
        self.assertEqual(claimed["job_id"], job_id)

    def test_mixed_legacy_program_is_not_reused_by_either_host(self) -> None:
        legacy = project_program_path(
            self.base_program,
            cwd=self.project,
            task_group="default",
        )
        workspace = ProgramWorkspace(legacy, repo="company-tools")
        with workspace.locked_manifest() as manifest:
            manifest["host"] = "codex"
            manifest["taxonomy_id"] = "tax-claude-native-mixed"
        for state_dir, session_id in (
            (".adamast-codex", "codex-legacy"),
            (".adamast-claude-code", "claude-legacy"),
        ):
            root = legacy / state_dir
            root.mkdir()
            (root / f"{session_id}.json").write_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "selection": {"status": "selected"},
                        "episode_sequence": 2,
                    }
                ),
                encoding="utf-8",
            )
        claude = self.config(project_scope="auto", task_group="default")
        codex = CodexConfig(
            trace_output=self.base_program,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            trace_root=self.root / "traces",
            dashboard=False,
            project_scope="auto",
            task_group="default",
            session_selector="prompt",
            learning_backend="codex_subagent",
        )

        claude_scoped = claude.for_event(
            {"cwd": str(self.project), "session_id": "claude-legacy"}
        )
        codex_scoped = codex.for_event(
            {"cwd": str(self.project), "session_id": "codex-legacy"}
        )

        self.assertNotEqual(claude_scoped.trace_output, legacy)
        self.assertNotEqual(codex_scoped.trace_output, legacy)
        self.assertTrue(claude_scoped.task_group.startswith("claude_code-branch-"))
        self.assertTrue(codex_scoped.task_group.startswith("codex-branch-"))

    def test_claude_worker_proposes_and_foreground_reconcile_activates(self) -> None:
        program = self.root / "program"
        trace_root = self.root / "worker-traces"
        taxonomy_store = self.root / "worker-taxonomies"
        workspace = ProgramWorkspace(program, repo="company-tools")
        workspace.pending.append_many_with_names(
            GenerationTrace(
                problem_id=f"episode-{index}",
                task=f"Task {index}",
                raw_trajectory=f"Failure evidence {index}",
            )
            for index in range(1, 6)
        )
        launched: list[Path] = []
        job_id = enqueue_claude_learning_job(
            workspace,
            kind="generation",
            store_dir=taxonomy_store,
            trace_root=trace_root,
            task_group="default",
            conversation_id="claude-session-1",
            claude_cli_path=sys.executable,
            launcher=launched.append,
        )
        job_dir = program / "learning_jobs" / job_id
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        candidate = {
            "decision": "replace",
            "repo": snapshot["repo"],
            "domain": "Small-company operations",
            "summary": "Recurring integration failures.",
            "codes": [
                {
                    "id": "OPS-1",
                    "name": "Demo boundary confusion",
                    "description": "A simulation is presented as a live integration.",
                    "category": "C",
                    "evidence": {
                        "trace_ids": [item["problem_id"] for item in snapshot["traces"]],
                        "quotes": [
                            {
                                "trace_id": item["problem_id"],
                                "quote": item["raw_trajectory"],
                            }
                            for item in snapshot["traces"]
                        ],
                        "rationale": "Every frozen episode exposed this boundary.",
                    },
                }
            ],
        }
        commands: list[str] = []

        def runner(command, **_kwargs):
            commands.extend(command)
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"structured_output": candidate}),
                stderr="",
            )

        self.assertEqual(run_worker(job_dir, runner=runner), 0)
        reconcile_learning_jobs(
            workspace,
            store_dir=taxonomy_store,
            trace_root=trace_root,
        )
        taxonomy_id = workspace.load()["taxonomy_id"]
        self.assertTrue(taxonomy_id.startswith("tax-claude-"))
        self.assertEqual(
            store.fetch_by_id(taxonomy_id, taxonomy_store)["provenance"]["driver"],
            "claude_subagent",
        )
        self.assertIn("--safe-mode", commands)
        self.assertIn("--tools", commands)
        self.assertIn("--no-session-persistence", commands)
        self.assertIn("--json-schema", commands)
        self.assertNotIn("ANTHROPIC_API_KEY", " ".join(commands))
        notices = drain_learning_notices(workspace, "claude-session-1")
        self.assertIn("taxonomy generation triggered", notices[0])
        self.assertIn("taxonomy generation finished", notices[-1])

    def test_native_agent_receipt_activates_without_standalone_cli(self) -> None:
        program = self.root / "native-program"
        taxonomy_store = self.root / "native-taxonomies"
        trace_root = self.root / "native-traces"
        workspace = ProgramWorkspace(program, repo="company-tools")
        workspace.pending.append_many_with_names(
            GenerationTrace(
                problem_id=f"native-episode-{index}",
                task=f"Task {index}",
                raw_trajectory=f"Grounded failure evidence {index}",
            )
            for index in range(1, 6)
        )
        job_id = enqueue_claude_learning_job(
            workspace,
            kind="generation",
            store_dir=taxonomy_store,
            trace_root=trace_root,
            task_group="default",
            conversation_id="claude-session-1",
            claude_cli_path=self.root / "missing-claude.exe",
        )
        job_dir = program / "learning_jobs" / job_id
        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["dispatch_mode"], "host_subagent")
        self.assertEqual(job["worker_driver"], "claude_native_subagent")
        self.assertTrue((job_dir / "prompt.txt").exists())
        self.assertTrue((job_dir / "output.schema.json").exists())

        with patch.dict(
            os.environ,
            {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
        ):
            self.assertIsNone(
                claim_learning_job(
                    workspace,
                    conversation_id="claude-session-1",
                )
            )
        dispatch = claim_learning_job(
            workspace,
            conversation_id="claude-session-1",
        )
        self.assertIn("Claude Code's native Agent tool", dispatch["directive"])
        self.assertIn("subagent_type=`adamast-taxonomy-worker`", dispatch["directive"])
        self.assertIn("run_in_background=`true`", dispatch["directive"])
        self.assertIn("Do not wait, join, poll, or delay", dispatch["directive"])
        self.assertTrue(dispatch["background_required"])
        self.assertIn("not claude -p", dispatch["directive"])
        snapshot = json.loads((job_dir / "snapshot.json").read_text(encoding="utf-8"))
        candidate = {
            "decision": "replace",
            "repo": snapshot["repo"],
            "domain": "Small-company operations",
            "summary": "Recurring integration failures.",
            "codes": [
                {
                    "id": "OPS-1",
                    "name": "Demo boundary confusion",
                    "description": "A simulation is presented as a live integration.",
                    "category": "C",
                    "evidence": {
                        "trace_ids": [
                            item["problem_id"] for item in snapshot["traces"]
                        ],
                        "quotes": [
                            {
                                "trace_id": item["problem_id"],
                                "quote": item["raw_trajectory"],
                            }
                            for item in snapshot["traces"]
                        ],
                        "rationale": "All frozen episodes expose this boundary.",
                    },
                }
            ],
        }
        receipt = {
            "version": 1,
            "job_id": job_id,
            "claim_token": dispatch["claim_token"],
            "status": "candidate",
            "candidate": candidate,
        }
        agent_transcript = self.root / "taxonomy-agent.jsonl"
        agent_transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    RECEIPT_OPEN
                                    + json.dumps(receipt, separators=(",", ":"))
                                    + RECEIPT_CLOSE
                                ),
                            }
                        ]
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        config = self.config(
            trace_output=program,
            store_dir=taxonomy_store,
            trace_root=trace_root,
            session_selector="off",
        )
        code, output = subagent_stop.handle(
            self.event(
                "SubagentStop",
                agent_id="taxonomy-agent",
                agent_transcript_path=str(agent_transcript),
            ),
            config,
        )
        self.assertEqual(code, 0)
        self.assertIn("proposal received", output["systemMessage"])
        reconcile_learning_jobs(
            workspace,
            store_dir=taxonomy_store,
            trace_root=trace_root,
        )
        reviewer = claim_learning_job(
            workspace,
            conversation_id="claude-session-1",
        )
        complete_support_review(
            job_dir,
            claim_token=reviewer["claim_token"],
            review={
                "supported": True,
                "codes": [
                    {
                        "id": "OPS-1",
                        "supported": True,
                        "reason": "The frozen episodes directly support this code.",
                        "trace_ids": [
                            item["problem_id"] for item in snapshot["traces"]
                        ],
                    }
                ],
            },
        )
        reconcile_learning_jobs(
            workspace,
            store_dir=taxonomy_store,
            trace_root=trace_root,
        )
        taxonomy_id = workspace.load()["taxonomy_id"]
        self.assertTrue(taxonomy_id.startswith("tax-claude-native-"))
        self.assertEqual(
            store.fetch_by_id(taxonomy_id, taxonomy_store)["provenance"]["driver"],
            "claude_native_subagent",
        )

    def test_polling_queues_generation_once_at_threshold(self) -> None:
        program = self.root / "poll-program"
        workspace = ProgramWorkspace(program, repo="company-tools")
        workspace.pending.append_many_with_names(
            GenerationTrace(
                problem_id=f"poll-{index}",
                task=f"Task {index}",
                raw_trajectory=f"Evidence {index}",
            )
            for index in range(1, 6)
        )
        kwargs = {
            "store_dir": self.root / "poll-taxonomies",
            "trace_root": self.root / "poll-traces",
            "task_group": "default",
            "conversation_id": "claude-session-1",
            "generation_threshold": 5,
            "k_init": 10,
            "k": 20,
            "freeze": False,
            "worker_model": None,
            "worker_timeout_seconds": 1800,
        }
        job_id = poll_learning_jobs(workspace, **kwargs)
        self.assertIsNotNone(job_id)
        self.assertIsNone(poll_learning_jobs(workspace, **kwargs))
        self.assertEqual(
            workspace.load()["interactive_learning"]["active_job_id"],
            job_id,
        )

    def test_mast_in_bound_project_creates_fresh_conversation_route(self) -> None:
        taxonomy_id = store.list_all(STORE_DIR)[0]["taxonomy_id"]
        ProgramWorkspace(self.base_program, repo="company-tools").bind_inherited_taxonomy(
            taxonomy_id
        )
        config = self.config(selector_surface="inline")
        event = self.event("SessionStart")
        output = session_start(event, config)
        self.assertIn("2. MAST", output["systemMessage"])
        accepted = user_prompt_submit(
            self.event("UserPromptSubmit", prompt="MAST"),
            config,
        )
        self.assertIn("AdaMAST selected MAST", accepted["systemMessage"])
        routed = config.for_event(event)
        self.assertNotEqual(routed.trace_output, self.base_program)
        self.assertTrue(routed.task_group.startswith("fresh-"))
        self.assertEqual(
            ProgramWorkspace(self.base_program).load()["taxonomy_id"],
            taxonomy_id,
        )
        self.assertEqual(
            load_state(routed.trace_output, "claude-session-1")["selection"][
                "status"
            ],
            "selected",
        )

    def test_browser_choice_binds_taxonomy_directly_to_claude_session(self) -> None:
        taxonomy_id = store.list_all(STORE_DIR)[0]["taxonomy_id"]
        selection = build_selection(
            trace_output=self.base_program,
            store_dir=STORE_DIR,
            cwd=self.project,
            catalog_mode="browser",
        )
        state = {
            "version": 1,
            "session_id": "claude-session-1",
            "selection": selection,
            "finished": True,
        }
        save_state(self.base_program, "claude-session-1", state)
        result_path = self.root / "browser-result.json"
        receipt = apply_browser_choice(
            {
                "session_id": "claude-session-1",
                "trace_output": str(self.base_program),
                "store_dir": str(STORE_DIR),
                "selection": selection,
                "event": {"cwd": str(self.project), "session_id": "claude-session-1"},
                "routing_root": str(self.base_program),
                "default_trace_output": str(self.base_program),
                "task_group": "default",
                "project_scope": "explicit",
                "project_id": None,
                "result_path": str(result_path),
            },
            taxonomy_id,
        )
        self.assertEqual(receipt["taxonomy_id"], taxonomy_id)
        self.assertEqual(
            ProgramWorkspace(self.base_program).load()["taxonomy_id"],
            taxonomy_id,
        )
        self.assertEqual(
            load_state(self.base_program, "claude-session-1")["selection"][
                "status"
            ],
            "selected",
        )

    def test_dispatcher_polls_and_injects_native_agent_directive(self) -> None:
        program = self.root / "dispatcher-program"
        workspace = ProgramWorkspace(program, repo="company-tools")
        workspace.pending.append_many_with_names(
            GenerationTrace(
                problem_id=f"dispatch-{index}",
                task=f"Task {index}",
                raw_trajectory=f"Evidence {index}",
            )
            for index in range(1, 6)
        )
        config = self.config(
            trace_output=program,
            store_dir=self.root / "dispatcher-taxonomies",
            trace_root=self.root / "dispatcher-traces",
            session_selector="off",
            project_scope="explicit",
            generation_threshold=5,
        )
        config_path = self.root / "adamast-claude.json"
        config_path.write_text(
            json.dumps(config.to_dict(), indent=2),
            encoding="utf-8",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        event = self.event("SessionStart")
        with patch("sys.stdin", io.StringIO(json.dumps(event))), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = dispatcher_main(["--config", str(config_path)])
        self.assertEqual(code, 0, stderr.getvalue())
        output = json.loads(stdout.getvalue())
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("AdaMAST native taxonomy learning is ready", context)
        self.assertIn("Claude Code's native Agent tool", context)
        jobs = list((program / "learning_jobs").glob("*/job.json"))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(
            json.loads(jobs[0].read_text(encoding="utf-8"))["state"],
            "claimed",
        )

    def test_detached_browser_process_applies_claude_choice(self) -> None:
        program = self.root / "detached-browser-program"
        session_id = "claude-browser-session"
        selection = build_selection(
            trace_output=program,
            store_dir=STORE_DIR,
            cwd=self.project,
            catalog_mode="browser",
        )
        selection["status"] = "browser_pending"
        save_state(
            program,
            session_id,
            {
                "version": 1,
                "session_id": session_id,
                "conversation_id": session_id,
                "selection": selection,
                "finished": True,
            },
        )
        taxonomy_id = store.list_all(STORE_DIR)[0]["taxonomy_id"]
        picker = start_browser_picker(
            program,
            session_id,
            store_dir=STORE_DIR,
            selection=selection,
            event={"cwd": str(self.project), "session_id": session_id},
            routing_root=self.root / "adamast-home",
            default_trace_output=program,
            task_group="default",
            project_scope="explicit",
            project_id=None,
            timeout_seconds=60,
        )
        try:
            with urlopen(picker["url"] + f"choose?id={taxonomy_id}", timeout=5) as response:
                response.read()
            deadline = time.monotonic() + 5
            state = {}
            while time.monotonic() < deadline:
                state = load_state(program, session_id)
                if state.get("selection", {}).get("status") == "selected":
                    break
                time.sleep(0.05)
            self.assertEqual(state["selection"]["status"], "selected")
            self.assertEqual(
                ProgramWorkspace(program).load()["taxonomy_id"],
                taxonomy_id,
            )
        finally:
            try:
                os.kill(int(picker["pid"]), signal.SIGTERM)
            except OSError:
                pass

    def test_run_claude_feeds_prompt_as_utf8_under_locale_codec(self) -> None:
        # Learning prompts routinely contain characters like U+2192 that the
        # Windows ANSI code page cannot encode; without an explicit UTF-8
        # stdin the writer thread died and the CLI exited 1 on empty input.
        harness = textwrap.dedent(
            """
            import sys
            from pathlib import Path

            from adamast.hosts.claude_code.native_worker import _run_claude

            echo = (
                "import sys;"
                "data = sys.stdin.buffer.read().decode('utf-8');"
                "sys.stdout.buffer.write(data.encode('utf-8'))"
            )
            completed = _run_claude(
                [sys.executable, "-c", echo],
                prompt="taxonomy \\u2192 worker",
                job_dir=Path(sys.argv[1]),
                timeout_seconds=60,
            )
            assert completed.returncode == 0, completed.stderr
            assert "\\u2192" in completed.stdout, ascii(completed.stdout)
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

    def test_notice_merging_is_visible_to_user_and_agent(self) -> None:
        merged = _merge_notices(
            None,
            event_name="Stop",
            notices=["AdaMAST taxonomy refinement finished"],
        )
        self.assertIn("refinement finished", merged["systemMessage"])
        self.assertIn(
            "refinement finished",
            merged["hookSpecificOutput"]["additionalContext"],
        )

    def test_user_level_install_preserves_unrelated_settings(self) -> None:
        claude_home = self.root / ".claude"
        claude_home.mkdir()
        settings_path = claude_home / "settings.json"
        settings_path.write_text(
            json.dumps({"effortLevel": "high", "enabledPlugins": {"demo": True}}),
            encoding="utf-8",
        )
        with patch(
            "adamast.hosts.claude_code.install.Path.home",
            return_value=self.root,
        ):
            result = install(
                self.root,
                self.config(project_scope="auto", selector_surface="browser"),
                verify=False,
                user_level=True,
            )
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(settings["effortLevel"], "high")
        self.assertTrue(settings["enabledPlugins"]["demo"])
        self.assertIn("UserPromptSubmit", settings["hooks"])
        prompt_hook = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        session_hook = settings["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(prompt_hook["timeout"], 1815)
        self.assertEqual(session_hook["timeout"], 60)
        self.assertEqual(result["scope"], "user")

        with patch(
            "adamast.hosts.claude_code.uninstall.Path.home",
            return_value=self.root,
        ):
            removed = uninstall(self.root, user_level=True)
        remaining = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(remaining["effortLevel"], "high")
        self.assertTrue(remaining["enabledPlugins"]["demo"])
        self.assertNotIn("hooks", remaining)
        self.assertTrue(removed["config_removed"])
        self.assertEqual(removed["scope"], "user")


if __name__ == "__main__":
    unittest.main()
