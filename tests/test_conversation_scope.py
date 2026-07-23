from __future__ import annotations

import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from adamast.hosts.claude_code.config import ClaudeCodeConfig
from adamast.hosts.claude_code.runtime import session_start, user_prompt_submit
from adamast.hosts.claude_code.state import load_state, save_state
from adamast.hosts.codex.config import CodexConfig
from adamast.hosts.codex.runtime import session_start as codex_session_start
from adamast.hosts.codex.runtime import (
    user_prompt_submit as codex_user_prompt_submit,
)
from adamast.hosts.codex.state import load_state as codex_load_state
from adamast.hosts.codex.state import save_state as save_codex_state
from adamast.hosts.interactive.browser_picker import (
    allowed_option,
    picker_alive,
    picker_page_context,
)
from adamast.hosts.interactive.selector import (
    build_selection,
    render_active_selection_context,
)
from adamast.hosts.interactive.source import stamp_program_source
from adamast import ProgramWorkspace
from adamast.core.program import ProgramConflict
from adamast.core.project_scope import project_program_path
from adamast.core import mast, store
from adamast.dashboard import webview


STORE_DIR = Path(__file__).resolve().parent / "fixtures" / "taxonomies"


class ConversationScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.routing_root = self.root / "adamast-home"
        self.project = self.root / "project"
        self.project.mkdir()
        self.transcript = self.root / "transcript.jsonl"
        self.transcript.write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def config(self, *, selector_surface: str) -> ClaudeCodeConfig:
        return ClaudeCodeConfig(
            trace_output=self.routing_root,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            trace_root=self.root / "traces",
            dashboard=False,
            project_scope="auto",
            session_selector="prompt",
            selector_surface=selector_surface,
            learning_backend="claude_subagent",
        )

    def event(self, name: str, cwd: Path) -> dict:
        return {
            "hook_event_name": name,
            "session_id": "claude-resume-session",
            "cwd": str(cwd),
            "transcript_path": str(self.transcript),
        }

    def test_selector_excludes_other_host_and_mixed_taxonomies(self) -> None:
        store_dir = self.root / "taxonomies"
        base = store.fetch_by_id("tax-django-orm-001", STORE_DIR)

        def register(taxonomy_id: str, *, source_host: str, driver: str) -> None:
            record = copy.deepcopy(base)
            record["taxonomy_id"] = taxonomy_id
            record["source"] = {"host": source_host}
            record["provenance"] = {"driver": driver}
            store.register(record, store_dir)

        neutral = copy.deepcopy(base)
        neutral["taxonomy_id"] = "tax-neutral-import"
        neutral.pop("source", None)
        neutral.pop("provenance", None)
        store.register(neutral, store_dir)
        register(
            "tax-codex-native-owned",
            source_host="Codex",
            driver="codex_native_subagent",
        )
        register(
            "tax-claude-native-owned",
            source_host="Claude Code",
            driver="claude_native_subagent",
        )
        register(
            "tax-claude-native-mixed",
            source_host="Codex",
            driver="claude_native_subagent",
        )
        program = self.root / "mixed-program"
        ProgramWorkspace(program).bind_inherited_taxonomy(
            "tax-claude-native-mixed"
        )

        selection = build_selection(
            trace_output=program,
            store_dir=store_dir,
            cwd=self.project,
            catalog_mode="inline",
            host="claude_code",
        )

        ids = {
            option.get("taxonomy_id")
            for option in selection["options"]
            if option.get("taxonomy_id")
        }
        self.assertEqual(
            selection["incompatible_project_taxonomy_id"],
            "tax-claude-native-mixed",
        )
        self.assertNotIn("tax-codex-native-owned", ids)
        self.assertNotIn("tax-claude-native-mixed", ids)
        self.assertIn("tax-claude-native-owned", ids)
        self.assertIn("tax-neutral-import", ids)
        mast_option = next(
            option for option in selection["options"] if option["kind"] == "mast"
        )
        self.assertTrue(mast_option["starts_fresh"])

        with self.assertRaisesRegex(ValueError, "not allowed"):
            allowed_option(
                {
                    "host": "claude_code",
                    "options": [
                        {
                            "kind": "taxonomy",
                            "taxonomy_id": "tax-codex-native-owned",
                            "label": "stale option",
                        }
                    ],
                },
                "tax-codex-native-owned",
                store_dir,
            )

    def test_program_cannot_be_claimed_by_both_interactive_hosts(self) -> None:
        workspace = ProgramWorkspace(self.root / "claimed-program")
        stamp_program_source(
            workspace,
            {"host": "Codex", "host_id": "codex"},
            store_dir=self.root / "taxonomies",
        )

        with self.assertRaisesRegex(ProgramConflict, "belongs to Codex"):
            stamp_program_source(
                workspace,
                {"host": "Claude Code", "host_id": "claude_code"},
                store_dir=self.root / "taxonomies",
            )

    def test_legacy_host_state_prevents_cross_host_claim(self) -> None:
        workspace = ProgramWorkspace(self.root / "legacy-program")
        (workspace.root / ".adamast-codex").mkdir()

        with self.assertRaisesRegex(ProgramConflict, "belongs to Codex"):
            stamp_program_source(
                workspace,
                {"host": "Claude Code", "host_id": "claude_code"},
                store_dir=self.root / "taxonomies",
            )

    def test_resumed_conversation_keeps_scope_after_cwd_changes(self) -> None:
        config = self.config(selector_surface="inline")
        original_event = self.event("SessionStart", self.project)
        original = config.for_event(original_event)
        session_start(original_event, original)
        user_prompt_submit(
            {**original_event, "hook_event_name": "UserPromptSubmit", "prompt": "MAST"},
            original,
        )
        other_project = self.root / "other-project"
        other_project.mkdir()
        resumed_event = self.event("SessionStart", other_project)

        resumed = config.for_event(resumed_event)

        self.assertEqual(resumed.trace_output, original.trace_output)
        with patch(
            "adamast.hosts.claude_code.runtime.start_browser_picker"
        ) as open_picker:
            output = session_start(resumed_event, resumed)
        open_picker.assert_not_called()
        self.assertIn(
            "AdaMAST taxonomy is pinned to MAST",
            output["hookSpecificOutput"]["additionalContext"],
        )

    def test_legacy_selected_state_migrates_before_resume_prompt(self) -> None:
        config = self.config(selector_surface="browser")
        original_program = project_program_path(
            self.routing_root,
            cwd=self.project,
            task_group="default",
        )
        selection = build_selection(
            trace_output=original_program,
            store_dir=STORE_DIR,
            cwd=self.project,
            catalog_mode="browser",
        )
        selection.update(
            status="selected",
            selected_kind="mast",
            selected_taxonomy_id=mast.MAST_ID,
            selected_label="MAST",
        )
        save_state(
            original_program,
            "claude-resume-session",
            {
                "version": 1,
                "session_id": "claude-resume-session",
                "conversation_id": "claude-resume-session",
                "cwd": str(self.project),
                "episode_sequence": 4,
                "selection": selection,
                "finished": True,
            },
        )
        other_project = self.root / "resume-shell"
        other_project.mkdir()
        resumed_event = self.event("SessionStart", other_project)

        resumed = config.for_event(resumed_event)

        self.assertEqual(resumed.trace_output, original_program)
        with patch(
            "adamast.hosts.claude_code.runtime.start_browser_picker"
        ) as open_picker:
            output = session_start(resumed_event, resumed)
        open_picker.assert_not_called()
        self.assertIn(
            "AdaMAST taxonomy is pinned to MAST",
            output["hookSpecificOutput"]["additionalContext"],
        )

    def test_codex_conversation_scope_is_stable_after_cwd_changes(self) -> None:
        config = CodexConfig(
            trace_output=self.routing_root,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            trace_root=self.root / "traces",
            dashboard=False,
            project_scope="auto",
            task_group="default",
        )
        first = config.for_event(
            {"thread_id": "codex-scope-thread", "cwd": str(self.project)}
        )
        other_project = self.root / "codex-other-project"
        other_project.mkdir()

        resumed = config.for_event(
            {"thread_id": "codex-scope-thread", "cwd": str(other_project)}
        )

        self.assertEqual(resumed.trace_output, first.trace_output)

    def test_learned_taxonomy_replaces_mast_in_host_context(self) -> None:
        config = CodexConfig(
            trace_output=self.routing_root,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            trace_root=self.root / "traces",
            dashboard=False,
            project_scope="auto",
            task_group="default",
            session_selector="prompt",
            selector_surface="inline",
        )
        event = {
            "hook_event_name": "SessionStart",
            "thread_id": "codex-learned-thread",
            "cwd": str(self.project),
            "transcript_path": str(self.transcript),
        }
        scoped = config.for_event(event)
        taxonomy_id = str(store.list_all(STORE_DIR)[0]["taxonomy_id"])
        record = store.fetch_by_id(taxonomy_id, STORE_DIR)
        ProgramWorkspace(scoped.trace_output).bind_inherited_taxonomy(taxonomy_id)
        selection = build_selection(
            trace_output=self.root / "unbound-selection",
            store_dir=STORE_DIR,
            cwd=self.project,
            catalog_mode="inline",
        )
        selection.update(
            status="selected",
            selected_kind="mast",
            selected_taxonomy_id=mast.MAST_ID,
            selected_label="MAST",
        )
        save_codex_state(
            scoped.trace_output,
            "codex-learned-thread",
            {
                "version": 1,
                "session_id": "codex-learned-thread",
                "conversation_id": "codex-learned-thread",
                "episode_sequence": 6,
                "taxonomy_id": taxonomy_id,
                "selection": selection,
                "finished": True,
            },
        )

        output = codex_session_start(event, scoped)
        context = output["hookSpecificOutput"]["additionalContext"]

        self.assertIn("AdaMAST active taxonomy is", context)
        self.assertIn(store.display_name(record), context)
        self.assertIn(taxonomy_id, context)
        self.assertIn("selected MAST lineage", context)
        self.assertIn("Use only codes from the active taxonomy", context)
        self.assertNotIn("taxonomy is pinned to MAST", context)

    def test_claude_context_names_learned_taxonomy_after_activation(self) -> None:
        config = self.config(selector_surface="inline")
        event = {
            "hook_event_name": "SessionStart",
            "session_id": "claude-learned-session",
            "cwd": str(self.project),
            "transcript_path": str(self.transcript),
        }
        scoped = config.for_event(event)
        taxonomy_id = str(store.list_all(STORE_DIR)[0]["taxonomy_id"])
        record = store.fetch_by_id(taxonomy_id, STORE_DIR)
        ProgramWorkspace(scoped.trace_output).bind_inherited_taxonomy(taxonomy_id)
        selection = build_selection(
            trace_output=self.root / "unbound-claude-selection",
            store_dir=STORE_DIR,
            cwd=self.project,
            catalog_mode="inline",
        )
        selection.update(
            status="selected",
            selected_kind="mast",
            selected_taxonomy_id=mast.MAST_ID,
            selected_label="MAST",
        )
        save_state(
            scoped.trace_output,
            "claude-learned-session",
            {
                "version": 1,
                "session_id": "claude-learned-session",
                "conversation_id": "claude-learned-session",
                "episode_sequence": 6,
                "taxonomy_id": taxonomy_id,
                "selection": selection,
                "finished": True,
            },
        )

        output = session_start(event, scoped)
        context = output["hookSpecificOutput"]["additionalContext"]

        self.assertIn("AdaMAST active taxonomy is", context)
        self.assertIn(store.display_name(record), context)
        self.assertIn(taxonomy_id, context)
        self.assertIn("selected MAST lineage", context)
        self.assertNotIn("taxonomy is pinned to MAST", context)

    def test_shared_context_preserves_seed_when_no_successor_is_active(self) -> None:
        selection = {
            "selected_taxonomy_id": mast.MAST_ID,
            "selected_label": "MAST",
        }

        context = render_active_selection_context(
            selection,
            active_taxonomy_id=mast.MAST_ID,
            store_dir=STORE_DIR,
        )

        self.assertIn("taxonomy is pinned to MAST", context)

    def test_claude_browser_selection_waits_for_first_user_prompt(self) -> None:
        event = {
            "hook_event_name": "SessionStart",
            "session_id": "claude-browser-defer",
            "cwd": str(self.project),
            "transcript_path": str(self.transcript),
        }
        config = self.config(selector_surface="browser").for_event(event)
        picker = {
            "pid": 4242,
            "url": "http://127.0.0.1:43210/",
            "result_path": str(self.root / "browser-result.json"),
        }
        with (
            patch(
                "adamast.hosts.claude_code.runtime.start_browser_picker",
                return_value=picker,
            ) as launch,
            patch(
                "adamast.hosts.claude_code.runtime.open_browser_picker",
                return_value=True,
            ) as opened,
            patch(
                "adamast.hosts.claude_code.runtime.wait_for_browser_choice",
                return_value="mast",
            ),
        ):
            started = session_start(event, config)
            self.assertIsNone(started)
            self.assertEqual(
                load_state(config.trace_output, event["session_id"]), {}
            )
            launch.assert_not_called()
            opened.assert_not_called()

            resumed = user_prompt_submit(
                {
                    **event,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "hey",
                },
                config,
            )
        launch.assert_called_once()
        opened.assert_called_once_with(picker)
        self.assertNotIn("decision", resumed)
        self.assertIn("selected MAST", resumed["systemMessage"])
        state = load_state(config.trace_output, event["session_id"])
        self.assertEqual(state["selection"]["status"], "selected")
        self.assertEqual(state["selection"]["pending_task"], "hey")
        self.assertEqual(state["episode_task"], "hey")

    def test_claude_browser_picker_relaunches_after_worker_death(self) -> None:
        session_id = "claude-browser-dead"
        event = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "cwd": str(self.project),
            "transcript_path": str(self.transcript),
        }
        config = self.config(selector_surface="browser").for_event(event)
        selection = build_selection(
            trace_output=config.trace_output,
            store_dir=STORE_DIR,
            cwd=self.project,
            catalog_mode="browser",
        )
        selection.update(
            status="browser_pending",
            browser_picker={
                "pid": 4243,
                "url": "http://127.0.0.1:43211/",
                "result_path": str(self.root / "missing-result.json"),
            },
        )
        save_state(
            config.trace_output,
            session_id,
            {
                "version": 1,
                "session_id": session_id,
                "conversation_id": session_id,
                "cwd": str(self.project),
                "episode_sequence": 0,
                "selection": selection,
                "finished": True,
            },
        )
        fresh_picker = {
            "pid": 4244,
            "url": "http://127.0.0.1:43212/",
            "result_path": str(self.root / "fresh-result.json"),
        }
        with (
            patch(
                "adamast.hosts.claude_code.runtime.picker_alive",
                return_value=False,
            ),
            patch(
                "adamast.hosts.claude_code.runtime.start_browser_picker",
                return_value=fresh_picker,
            ) as relaunch,
            patch(
                "adamast.hosts.claude_code.runtime.open_browser_picker",
                return_value=True,
            ) as opened,
            patch(
                "adamast.hosts.claude_code.runtime.wait_for_browser_choice",
                return_value=None,
            ),
        ):
            output = user_prompt_submit({**event, "prompt": "hey again"}, config)
        relaunch.assert_called_once()
        opened.assert_called_once_with(fresh_picker)
        self.assertEqual(output["decision"], "block")
        state = load_state(config.trace_output, session_id)
        self.assertEqual(
            state["selection"]["browser_picker"]["url"],
            fresh_picker["url"],
        )
        self.assertEqual(state["selection"]["pending_task"], "hey again")

        with (
            patch(
                "adamast.hosts.claude_code.runtime.picker_alive",
                return_value=True,
            ),
            patch(
                "adamast.hosts.claude_code.runtime.start_browser_picker"
            ) as relaunch,
            patch(
                "adamast.hosts.claude_code.runtime.open_browser_picker"
            ) as opened,
            patch(
                "adamast.hosts.claude_code.runtime.wait_for_browser_choice",
                return_value="mast",
            ),
        ):
            waiting = user_prompt_submit({**event, "prompt": "still here"}, config)
        relaunch.assert_not_called()
        opened.assert_not_called()
        self.assertNotIn("decision", waiting)
        self.assertIn("selected MAST", waiting["systemMessage"])

    def test_codex_browser_picker_relaunches_after_worker_death(self) -> None:
        thread_id = "codex-browser-dead"
        event = {
            "hook_event_name": "UserPromptSubmit",
            "thread_id": thread_id,
            "cwd": str(self.project),
            "transcript_path": str(self.transcript),
            "prompt": "hey codex",
        }
        config = CodexConfig(
            trace_output=self.routing_root,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            trace_root=self.root / "traces",
            dashboard=False,
            project_scope="auto",
            task_group="default",
            session_selector="prompt",
            selector_surface="browser",
        ).for_event(event)
        selection = build_selection(
            trace_output=config.trace_output,
            store_dir=STORE_DIR,
            cwd=self.project,
            catalog_mode="browser",
        )
        selection.update(
            status="browser_pending",
            browser_picker={
                "pid": 4245,
                "url": "http://127.0.0.1:43213/",
                "result_path": str(self.root / "missing-codex-result.json"),
            },
        )
        save_codex_state(
            config.trace_output,
            thread_id,
            {
                "version": 1,
                "session_id": thread_id,
                "conversation_id": thread_id,
                "episode_sequence": 0,
                "selection": selection,
                "finished": True,
            },
        )
        fresh_picker = {
            "pid": 4246,
            "url": "http://127.0.0.1:43214/",
            "result_path": str(self.root / "fresh-codex-result.json"),
        }
        with (
            patch(
                "adamast.hosts.codex.runtime.picker_alive",
                return_value=False,
            ),
            patch(
                "adamast.hosts.codex.runtime.start_browser_picker",
                return_value=fresh_picker,
            ) as relaunch,
            patch(
                "adamast.hosts.codex.runtime.open_browser_picker",
                return_value=True,
            ) as opened,
            patch(
                "adamast.hosts.codex.runtime.wait_for_browser_choice",
                return_value=None,
            ),
        ):
            output = codex_user_prompt_submit(event, config)
        relaunch.assert_called_once()
        opened.assert_called_once_with(fresh_picker)
        self.assertFalse(output["continue"])
        self.assertIn("timed out", output["stopReason"])
        state = codex_load_state(config.trace_output, thread_id)
        self.assertEqual(
            state["selection"]["browser_picker"]["url"],
            fresh_picker["url"],
        )
        self.assertEqual(state["selection"]["pending_task"], "hey codex")

        with (
            patch(
                "adamast.hosts.codex.runtime.picker_alive",
                return_value=True,
            ),
            patch(
                "adamast.hosts.codex.runtime.start_browser_picker"
            ) as relaunch,
            patch(
                "adamast.hosts.codex.runtime.open_browser_picker"
            ) as opened,
            patch(
                "adamast.hosts.codex.runtime.wait_for_browser_choice",
                return_value="mast",
            ),
        ):
            resumed = codex_user_prompt_submit(
                {**event, "prompt": "selection complete"},
                config,
            )
        relaunch.assert_not_called()
        opened.assert_not_called()
        self.assertNotIn("continue", resumed)
        self.assertIn("selected MAST", resumed["systemMessage"])

    def test_picker_page_context_carries_session_identity(self) -> None:
        request = {
            "session_id": "claude-session-xyz",
            "event": {"cwd": str(self.project)},
            "selection": {
                "project": "project",
                "project_root": str(self.project),
                "pending_task": "hey",
            },
        }

        context = picker_page_context(request, host_label="Claude Code")

        self.assertEqual(context["session_id"], "claude-session-xyz")
        self.assertEqual(context["session_prompt"], "hey")
        self.assertEqual(context["session_cwd"], str(self.project))
        self.assertEqual(context["host_label"], "Claude Code")
        self.assertIsNone(picker_page_context(None, host_label="Claude Code"))

    def test_picker_alive_reflects_worker_liveness(self) -> None:
        server, _result, _done = webview.build_server(STORE_DIR)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        try:
            self.assertTrue(picker_alive({"url": url}))
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
        self.assertFalse(picker_alive({"url": url}))
        self.assertFalse(picker_alive(None))
        self.assertFalse(picker_alive({}))

    def test_picker_page_names_the_requesting_session(self) -> None:
        choice = {
            "kind": "mast",
            "taxonomy_id": mast.MAST_ID,
            "label": "MAST",
            "description": "Built-in taxonomy",
            "domain": "General agent work",
            "origin": "Built-in",
        }
        server, _result, done = webview.build_server(
            STORE_DIR,
            choice_options=[choice],
            picker_context={
                "host_label": "Claude Code",
                "session_id": "f4bdc749-86d7-4cb4-8ddd-6c2df2a5a2a4",
                "session_prompt": "inspect the flux capacitor readings",
            },
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/"
            with urlopen(url, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertIn("Session", body)
            self.assertIn("f4bdc749", body)
            self.assertNotIn("f4bdc749-86d7", body)
            self.assertIn("First message", body)
            self.assertIn("inspect the flux capacitor readings", body)
            with urlopen(url + "choose?id=mast", timeout=5) as response:
                success = response.read().decode("utf-8")
            self.assertIn("for session f4bdc749", success)
            self.assertTrue(done.wait(timeout=1))
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_picker_completion_names_the_active_host(self) -> None:
        choice = {
            "kind": "mast",
            "taxonomy_id": mast.MAST_ID,
            "label": "MAST",
            "description": "Built-in taxonomy",
            "domain": "General agent work",
            "origin": "Built-in",
        }
        for host_label in ("Claude Code", "Codex"):
            server, _result, done = webview.build_server(
                STORE_DIR,
                choice_options=[choice],
                picker_context={"host_label": host_label},
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/"
                with urlopen(url + "choose?id=mast", timeout=5) as response:
                    body = response.read().decode("utf-8")
                self.assertIn(f"Return to {host_label}", body)
                self.assertIn("original task will continue automatically", body)
                self.assertNotIn("next lifecycle event", body)
                self.assertTrue(done.wait(timeout=1))
            finally:
                server.shutdown()
                thread.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
