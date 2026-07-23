"""Web-view tests: drive the real HTTP server, no browser needed."""

import json
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from adamast.dashboard import webview
from adamast.hosts.codex.browser_picker import (
    apply_browser_choice,
    serve_picker,
    start_browser_picker,
)
from adamast.hosts.codex.state import load_state, save_state
from adamast.hosts.codex.session_routes import resolve_session_route
from adamast.hosts.interactive.selector import build_selection
from adamast import ProgramWorkspace

STORE_DIR = Path(__file__).resolve().parent / "fixtures" / "taxonomies"


class WebViewTests(unittest.TestCase):
    def setUp(self):
        self.server, self.result, self.done = webview.build_server(STORE_DIR)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def _get(self, path):
        with urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
            return resp.read().decode("utf-8")

    def test_catalog_has_human_facing_workspace_and_is_global(self):
        body = self._get("/")
        self.assertIn('class="library-pane"', body)
        self.assertIn('class="detail-pane"', body)
        self.assertIn("Select the failure model", body)
        self.assertIn("Name, domain, or project", body)
        self.assertIn("tax-django-orm-001", body)
        # global across repos: rows from more than one repo present
        self.assertIn("django/django", body)
        self.assertIn("numpy/numpy", body)

    def test_detail_shows_full_content(self):
        body = self._get("/taxonomy/tax-numpy-array-003")
        self.assertIn("View vs copy aliasing mutation", body)      # name
        self.assertIn("broadcast", body.lower())                   # explanation
        self.assertIn("b = a[::2]", body)                          # extra field

    def test_choose_id_records_choice_and_finishes(self):
        self._get("/choose?id=tax-flask-routing-004")
        self.assertTrue(self.done.wait(timeout=2))
        self.assertEqual(self.result["value"], "tax-flask-routing-004")

    def test_choose_none_path(self):
        self._get(f"/choose?id={webview.NONE_SENTINEL}")
        self.assertTrue(self.done.wait(timeout=2))
        self.assertEqual(self.result["value"], "none")


class RenderTableBraceRegressionTests(unittest.TestCase):
    def test_braces_in_repo_and_domain_do_not_crash_table(self):
        # Regression: repo/domain carrying `{`/`}` used to blow up the picker
        # index because `.format` ran over the already-interpolated rows.
        import tempfile

        from adamast.core import store

        store_dir = Path(tempfile.mkdtemp())
        store.register(
            {
                "taxonomy_id": "t-braces",
                "repo": "acme/{svc}",
                "domain": "web {SPA}",
                "codes": [
                    {"id": "A.1", "name": "n", "description": "d", "category": "A"}
                ],
            },
            store_dir,
        )
        html = webview._render_table(store_dir)
        self.assertIn("acme/{svc}", html)
        self.assertIn("web {SPA}", html)

    def test_explicit_display_name_hides_hash_as_primary_label(self):
        import tempfile

        from adamast.core import store

        store_dir = Path(tempfile.mkdtemp())
        store.register(
            {
                "taxonomy_id": "tax-hashed-123",
                "display_name": "Billing Workflow Reliability",
                "repo": "billing",
                "domain": "payment operations",
                "codes": [
                    {"id": "A.1", "name": "n", "description": "d", "category": "A"}
                ],
            },
            store_dir,
        )
        body = webview._render_table(store_dir)
        self.assertIn("Billing Workflow Reliability", body)
        self.assertIn('<span class="item-id">tax-hashed-123</span>', body)


class CodexBrowserReceiptTests(unittest.TestCase):
    def test_session_bound_choice_applies_without_a_user_prompt_hook(self):
        root = Path(tempfile.mkdtemp())
        program = root / "program"
        result_path = root / "result.json"
        session_id = "browser-direct-session"
        selection = build_selection(
            trace_output=program,
            store_dir=STORE_DIR,
            cwd=root,
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
        request = {
            "version": 1,
            "session_id": session_id,
            "trace_output": str(program),
            "store_dir": str(STORE_DIR),
            "selection": selection,
            "event": {"cwd": str(root), "session_id": session_id},
            "routing_root": str(root / "adamast-home"),
            "default_trace_output": str(program),
            "task_group": "default",
            "project_scope": "explicit",
            "project_id": None,
            "result_path": str(result_path),
        }

        receipt = apply_browser_choice(request, "tax-django-orm-001")

        self.assertEqual(receipt["status"], "selected")
        state = load_state(program, session_id)
        self.assertEqual(state["selection"]["status"], "selected")
        self.assertEqual(
            state["selection"]["selected_taxonomy_id"],
            "tax-django-orm-001",
        )
        self.assertEqual(
            ProgramWorkspace(program).load()["taxonomy_id"],
            "tax-django-orm-001",
        )
        self.assertEqual(
            json.loads(result_path.read_text(encoding="utf-8"))["choice"],
            "tax-django-orm-001",
        )

    def test_mast_choice_preserves_bound_project_and_routes_fresh(self):
        root = Path(tempfile.mkdtemp())
        routing_root = root / "adamast-home"
        program = routing_root / "shared-program"
        result_path = root / "mast-result.json"
        session_id = "browser-fresh-mast"
        ProgramWorkspace(program).bind_inherited_taxonomy("tax-django-orm-001")
        selection = build_selection(
            trace_output=program,
            store_dir=STORE_DIR,
            cwd=root,
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
        event = {"cwd": str(root), "session_id": session_id}
        request = {
            "version": 1,
            "session_id": session_id,
            "trace_output": str(program),
            "store_dir": str(STORE_DIR),
            "selection": selection,
            "event": event,
            "routing_root": str(routing_root),
            "default_trace_output": str(program),
            "task_group": "default",
            "project_scope": "explicit",
            "project_id": None,
            "result_path": str(result_path),
        }

        receipt = apply_browser_choice(request, "mast")

        route = resolve_session_route(
            routing_root,
            event,
            default_trace_output=program,
        )
        self.assertIsNotNone(route)
        self.assertEqual(receipt["trace_output"], str(route.trace_output))
        self.assertEqual(
            ProgramWorkspace(program).load()["taxonomy_id"],
            "tax-django-orm-001",
        )
        self.assertEqual(
            load_state(program, session_id)["selection"]["status"],
            "routed",
        )
        routed_state = load_state(route.trace_output, session_id)
        self.assertEqual(routed_state["selection"]["status"], "selected")
        self.assertEqual(
            routed_state["selection"]["fresh_task_group"],
            route.task_group,
        )

    def test_non_blocking_picker_writes_durable_choice_receipt(self):
        root = Path(tempfile.mkdtemp())
        ready_path = root / "ready.json"
        result_path = root / "result.json"
        exit_codes = []

        with patch(
            "adamast.hosts.codex.browser_picker.webbrowser.open",
            return_value=True,
        ):
            thread = threading.Thread(
                target=lambda: exit_codes.append(
                    serve_picker(
                        store_dir=STORE_DIR,
                        ready_path=ready_path,
                        result_path=result_path,
                        timeout_seconds=60,
                    )
                ),
                daemon=True,
            )
            thread.start()
            deadline = time.monotonic() + 3
            while not ready_path.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            ready = json.loads(ready_path.read_text(encoding="utf-8"))
            with urlopen(
                ready["url"] + "choose?id=tax-flask-routing-004"
            ) as response:
                response.read()
            thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(exit_codes, [0])
        self.assertEqual(
            json.loads(result_path.read_text(encoding="utf-8"))["taxonomy_id"],
            "tax-flask-routing-004",
        )

    def test_detached_codex_picker_applies_http_choice_to_session(self):
        root = Path(tempfile.mkdtemp())
        program = root / "program"
        session_id = "detached-picker-session"
        selection = build_selection(
            trace_output=program,
            store_dir=STORE_DIR,
            cwd=root,
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
        picker = start_browser_picker(
            program,
            session_id,
            store_dir=STORE_DIR,
            selection=selection,
            event={"cwd": str(root), "session_id": session_id},
            routing_root=root / "adamast-home",
            default_trace_output=program,
            timeout_seconds=60,
        )
        try:
            with urlopen(
                picker["url"] + "choose?id=tax-flask-routing-004",
                timeout=5,
            ) as response:
                success_page = response.read().decode("utf-8")
            self.assertIn("Return to Codex", success_page)
            deadline = time.monotonic() + 5
            state = {}
            while time.monotonic() < deadline:
                state = load_state(program, session_id)
                if state.get("selection", {}).get("status") == "selected":
                    break
                time.sleep(0.05)

            self.assertEqual(state["selection"]["status"], "selected")
            self.assertEqual(
                state["selection"]["selected_taxonomy_id"],
                "tax-flask-routing-004",
            )
            self.assertEqual(
                ProgramWorkspace(program).load()["taxonomy_id"],
                "tax-flask-routing-004",
            )
            self.assertEqual(
                json.loads(
                    Path(picker["result_path"]).read_text(encoding="utf-8")
                )["choice"],
                "tax-flask-routing-004",
            )
        finally:
            try:
                os.kill(int(picker["pid"]), signal.SIGTERM)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
