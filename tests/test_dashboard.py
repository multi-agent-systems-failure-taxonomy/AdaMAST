"""Persistent live taxonomy dashboard tests."""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from adamast.dashboard.server import (
    DEFAULT_READY_TIMEOUT,
    RUNTIME_EVIDENCE,
    _ready_timeout,
    build_server,
    current_taxonomy,
    ensure_dashboard,
    monitor_snapshot,
    stop_dashboard,
)
from adamast.core.lineage import TaxonomyLineage
from adamast.core.program import ProgramWorkspace

ROOT = Path(__file__).resolve().parent.parent
BASE_STORE = ROOT / "tests" / "fixtures" / "taxonomies"
BASE_ID = "tax-django-orm-001"
NEXT_ID = "tax-django-orm-live-002"


def copy_store(destination: Path) -> None:
    destination.mkdir()
    for source in BASE_STORE.glob("*.json"):
        (destination / source.name).write_bytes(source.read_bytes())


class DashboardDataTests(unittest.TestCase):
    def test_monitor_discovers_claude_state_and_display_title(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            monitor_root = root / "interactive"
            program = (
                monitor_root
                / "projects"
                / "demo-a1b2"
                / "groups"
                / "default"
                / "program"
            )
            workspace = ProgramWorkspace(program, repo="owner/demo")
            transcript = root / "claude-session.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "custom-title",
                        "sessionId": "claude-conversation",
                        "customTitle": "Review the AdaMAST runtime",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state_dir = program / ".adamast-claude-code"
            state_dir.mkdir()
            (state_dir / "claude-conversation.json").write_text(
                json.dumps(
                    {
                        "session_id": "claude-conversation",
                        "conversation_id": "claude-conversation",
                        "conversation_title": "Conversation claude-c",
                        "conversation_host": "claude_code",
                        "transcript_path": str(transcript),
                        "episode_sequence": 2,
                        "finished": False,
                    }
                ),
                encoding="utf-8",
            )

            data = monitor_snapshot(
                monitor_root,
                workspace.root,
                BASE_STORE,
                conversation_id="claude-conversation",
            )

            conversation = data["conversations"][0]
            self.assertEqual(conversation["title"], "Review the AdaMAST runtime")
            self.assertEqual(conversation["host"], "claude_code")
            self.assertEqual(conversation["host_label"], "Claude Code")

    def test_monitor_refreshes_title_from_codex_session_index(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            monitor_root = root / "interactive"
            program = (
                monitor_root
                / "projects"
                / "demo-a1b2"
                / "groups"
                / "default"
                / "program"
            )
            workspace = ProgramWorkspace(program, repo="owner/demo")
            state_dir = program / ".adamast-codex"
            state_dir.mkdir()
            (state_dir / "named-conversation.json").write_text(
                json.dumps(
                    {
                        "session_id": "named-conversation",
                        "conversation_title": "Conversation named-co",
                        "finished": False,
                    }
                ),
                encoding="utf-8",
            )
            codex_home = root / ".codex"
            codex_home.mkdir()
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": "named-conversation",
                        "thread_name": "Review the AdaMAST runtime",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}):
                data = monitor_snapshot(
                    monitor_root,
                    workspace.root,
                    BASE_STORE,
                    conversation_id="named-conversation",
                )

            self.assertEqual(
                data["conversations"][0]["title"],
                "Review the AdaMAST runtime",
            )

    def test_unbound_program_shows_mast(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = ProgramWorkspace(Path(td) / "program")
            data = current_taxonomy(workspace, BASE_STORE)
            self.assertEqual(data["taxonomy_id"], "mast")
            self.assertEqual(len(data["codes"]), 14)
            self.assertEqual(data["codes"][0]["code_id"], "MAST-1")

    def test_unbound_program_uses_program_repo_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = ProgramWorkspace(
                Path(td) / "program",
                repo="owner/project",
            )
            data = current_taxonomy(workspace, BASE_STORE)
            self.assertEqual(data["repo"], "owner/project")

    def test_bound_program_resolves_latest_successor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store_dir = root / "taxonomies"
            copy_store(store_dir)
            workspace = ProgramWorkspace(root / "program")
            workspace.bind_inherited_taxonomy(BASE_ID)
            original = json.loads(
                (store_dir / f"{BASE_ID}.json").read_text(encoding="utf-8")
            )
            successor = {**original, "taxonomy_id": NEXT_ID}
            (store_dir / f"{NEXT_ID}.json").write_text(
                json.dumps(successor), encoding="utf-8"
            )
            TaxonomyLineage(store_dir).add_successor(BASE_ID, NEXT_ID)

            data = current_taxonomy(workspace, store_dir)
            self.assertEqual(data["bound_taxonomy_id"], BASE_ID)
            self.assertEqual(data["taxonomy_id"], NEXT_ID)
            self.assertTrue(data["is_latest_successor"])

    def test_conversation_branch_keeps_its_exact_head(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store_dir = root / "taxonomies"
            copy_store(store_dir)
            workspace = ProgramWorkspace(root / "program")
            workspace.bind_conversation_branch(
                "codex-branch-dashboard",
                conversation_id="conversation-dashboard",
                host="codex",
            )
            workspace.bind_inherited_taxonomy(BASE_ID)
            original = json.loads(
                (store_dir / f"{BASE_ID}.json").read_text(encoding="utf-8")
            )
            successor = {**original, "taxonomy_id": NEXT_ID}
            (store_dir / f"{NEXT_ID}.json").write_text(
                json.dumps(successor), encoding="utf-8"
            )
            TaxonomyLineage(store_dir).add_successor(
                BASE_ID,
                NEXT_ID,
                branch_id="another-branch",
            )

            data = current_taxonomy(workspace, store_dir)

            self.assertEqual(data["taxonomy_id"], BASE_ID)
            self.assertFalse(data["is_latest_successor"])

    def test_program_runtime_evidence_overlays_without_mutating_taxonomy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = ProgramWorkspace(root / "program")
            (workspace.root / RUNTIME_EVIDENCE).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "checkpoints": [
                            {
                                "taxonomy_id": "mast",
                                "checkpoint_id": "cp-1",
                                "timestamp": 1,
                                "fired_codes": ["MAST-1"],
                            }
                        ],
                        "taxonomies": {
                            "mast": {
                                "codes": {
                                    "MAST-1": {
                                        "fire_count": 2,
                                        "task_firings": {"task-a": 2},
                                        "events": [
                                            {
                                                "checkpoint_id": "cp-1",
                                                "timestamp": 1,
                                                "evidence": "ignored spec",
                                                "correlate": "genuine mismatch",
                                            }
                                        ],
                                    }
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            data = current_taxonomy(workspace, BASE_STORE)
            first = data["codes"][0]
            self.assertEqual(first["fire_count"], 2)
            self.assertEqual(
                first["task_firings"],
                [{"task_id": "task-a", "label": "task-a", "count": 2}],
            )
            self.assertEqual(
                first["runtime_evidence"][0]["evidence"],
                "ignored spec",
            )
            self.assertEqual(first["runtime_evidence"][0]["seq"], 1)

    def test_task_labels_and_evidence_clipping(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = ProgramWorkspace(root / "program")
            (workspace.root / ".adamast-task-labels.json").write_text(
                json.dumps(
                    {"sess-xyz": {"label": "UID0001", "correct": True}}
                ),
                encoding="utf-8",
            )
            long_evidence = "x" * 600
            (workspace.root / RUNTIME_EVIDENCE).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "taxonomies": {
                            "mast": {
                                "codes": {
                                    "MAST-1": {
                                        "fire_count": 1,
                                        "task_firings": {"sess-xyz": 1},
                                        "events": [
                                            {
                                                "task_id": "sess-xyz",
                                                "evidence": long_evidence,
                                            }
                                        ],
                                    }
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            data = current_taxonomy(workspace, BASE_STORE)
            first = data["codes"][0]
            self.assertEqual(first["task_firings"][0]["label"], "UID0001 ✓")
            event = first["runtime_evidence"][0]
            self.assertEqual(event["task_label"], "UID0001 ✓")
            self.assertEqual(event["evidence"], long_evidence)

    def test_clean_checkpoints_are_exposed_with_sequence_labels(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = ProgramWorkspace(root / "program")
            (workspace.root / ".adamast-task-labels.json").write_text(
                json.dumps({"task-clean": "Dataset item 7"}),
                encoding="utf-8",
            )
            (workspace.root / RUNTIME_EVIDENCE).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "checkpoints": [
                            {
                                "taxonomy_id": "mast",
                                "checkpoint_id": "cp-fired",
                                "timestamp": 1,
                                "gate": "task_completed",
                                "task_id": "task-fired",
                                "fired_codes": ["MAST-1"],
                            },
                            {
                                "taxonomy_id": "mast",
                                "checkpoint_id": "cp-clean",
                                "timestamp": 2,
                                "gate": "stop",
                                "task_id": "task-clean",
                                "none_apply": True,
                                "considered_codes": ["MAST-1", "MAST-12"],
                                "fired_codes": [],
                                "observe": "everything relevant was checked",
                                "correlate": "no root failure found",
                                "decide": "no change needed, because checks passed",
                            },
                        ],
                        "taxonomies": {},
                    }
                ),
                encoding="utf-8",
            )

            data = current_taxonomy(workspace, BASE_STORE)

            self.assertEqual(len(data["clean_checkpoints"]), 1)
            clean = data["clean_checkpoints"][0]
            self.assertEqual(clean["seq"], 2)
            self.assertEqual(clean["checkpoint_id"], "cp-clean")
            self.assertEqual(clean["task_label"], "Dataset item 7")
            self.assertTrue(clean["none_apply"])
            self.assertEqual(clean["considered"], ["MAST-1", "MAST-12"])

    def test_uid_labels_are_available_for_task_filtering(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = ProgramWorkspace(root / "program")
            (workspace.root / ".adamast-task-labels.json").write_text(
                json.dumps(
                    {
                        "sess-0118": {"label": "UID0118", "correct": False},
                        "sess-0222": {"label": "UID0222", "correct": True},
                    }
                ),
                encoding="utf-8",
            )
            (workspace.root / RUNTIME_EVIDENCE).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "checkpoints": [
                            {
                                "taxonomy_id": "mast",
                                "checkpoint_id": "cp-a",
                                "timestamp": 1,
                                "gate": "stop",
                                "task_id": "sess-0118",
                                "fired_codes": ["MAST-1", "MAST-2"],
                            },
                            {
                                "taxonomy_id": "mast",
                                "checkpoint_id": "cp-b",
                                "timestamp": 2,
                                "gate": "stop",
                                "task_id": "sess-0222",
                                "fired_codes": ["MAST-3"],
                            },
                            {
                                "taxonomy_id": "mast",
                                "checkpoint_id": "cp-clean",
                                "timestamp": 3,
                                "gate": "stop",
                                "task_id": "sess-0118",
                                "none_apply": True,
                                "considered_codes": ["MAST-4"],
                                "fired_codes": [],
                            },
                        ],
                        "taxonomies": {
                            "mast": {
                                "codes": {
                                    "MAST-1": {
                                        "fire_count": 1,
                                        "task_firings": {"sess-0118": 1},
                                        "events": [
                                            {
                                                "checkpoint_id": "cp-a",
                                                "task_id": "sess-0118",
                                                "evidence": "failure one",
                                            }
                                        ],
                                    },
                                    "MAST-2": {
                                        "fire_count": 1,
                                        "task_firings": {"sess-0118": 1},
                                        "events": [
                                            {
                                                "checkpoint_id": "cp-a",
                                                "task_id": "sess-0118",
                                                "evidence": "failure two",
                                            }
                                        ],
                                    },
                                    "MAST-3": {
                                        "fire_count": 1,
                                        "task_firings": {"sess-0222": 1},
                                        "events": [
                                            {
                                                "checkpoint_id": "cp-b",
                                                "task_id": "sess-0222",
                                                "evidence": "other task",
                                            }
                                        ],
                                    },
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            data = current_taxonomy(workspace, BASE_STORE)

            by_id = {code["code_id"]: code for code in data["codes"]}
            self.assertEqual(
                by_id["MAST-1"]["task_firings"][0]["label"],
                "UID0118 ✗",
            )
            self.assertEqual(
                by_id["MAST-2"]["runtime_evidence"][0]["task_label"],
                "UID0118 ✗",
            )
            self.assertEqual(
                by_id["MAST-3"]["task_firings"][0]["label"],
                "UID0222 ✓",
            )
            self.assertEqual(
                data["clean_checkpoints"][0]["task_label"],
                "UID0118 ✗",
            )


    def test_monitor_discovers_project_conversation_and_checkpoint_coordinates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "interactive"
            program = (
                root
                / "projects"
                / "demo-a1b2"
                / "groups"
                / "default"
                / "program"
            )
            workspace = ProgramWorkspace(program, repo="owner/demo")
            platform_program = (
                root
                / "projects"
                / "demo-a1b2"
                / "groups"
                / "platform"
                / "program"
            )
            ProgramWorkspace(platform_program, repo="owner/demo")
            state_dir = program / ".adamast-codex"
            state_dir.mkdir()
            (state_dir / "conversation-one.json").write_text(
                json.dumps(
                    {
                        "session_id": "conversation-one",
                        "conversation_id": "conversation-one",
                        "conversation_title": "Repair the release workflow",
                        "task_group": "default",
                        "episode_sequence": 3,
                        "finished": False,
                        "created_at": "2026-07-21T10:00:00+00:00",
                        "updated_at": "2026-07-21T11:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (program / RUNTIME_EVIDENCE).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "checkpoints": [
                            {
                                "taxonomy_id": "mast",
                                "checkpoint_id": "cp-monitor",
                                "conversation_id": "conversation-one",
                                "session_id": "conversation-one",
                                "episode_sequence": 3,
                                "turn_id": "turn-003",
                                "timestamp": 100,
                                "gate": "stop",
                                "gate_status": "READY_TO_SUBMIT",
                                "none_apply": False,
                                "considered_codes": ["MAST-1"],
                                "fired_codes": ["MAST-1"],
                                "observe": "Release choice is still pending",
                                "correlate": "The package and repository differ",
                                "decide": "await the release choice",
                            },
                            {
                                "taxonomy_id": "mast",
                                "checkpoint_id": "cp-clean-later",
                                "conversation_id": "conversation-one",
                                "session_id": "conversation-one",
                                "episode_sequence": 4,
                                "turn_id": "turn-004",
                                "timestamp": 200,
                                "gate": "stop",
                                "gate_status": "READY_TO_SUBMIT",
                                "none_apply": True,
                                "considered_codes": [],
                                "fired_codes": [],
                                "observe": "Release workflow verified",
                                "correlate": "No supported failure remains",
                                "decide": "complete",
                            }
                        ],
                        "taxonomies": {
                            "mast": {
                                "codes": {
                                    "MAST-1": {
                                        "events": [
                                            {
                                                "checkpoint_id": "cp-monitor",
                                                "evidence": "release targets differ",
                                            }
                                        ]
                                    }
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            platform_state = platform_program / ".adamast-codex"
            platform_state.mkdir()
            (platform_state / "conversation-platform.json").write_text(
                json.dumps(
                    {
                        "session_id": "conversation-platform",
                        "conversation_id": "conversation-platform",
                        "conversation_title": "Repair the platform runtime",
                        "task_group": "platform",
                        "episode_sequence": 1,
                        "finished": True,
                    }
                ),
                encoding="utf-8",
            )

            data = monitor_snapshot(
                root,
                workspace.root,
                BASE_STORE,
                project_id="demo-a1b2",
                conversation_id="conversation-one",
            )

            self.assertEqual(data["selection"]["project_id"], "demo-a1b2")
            self.assertEqual(
                data["selection"]["conversation_id"], "conversation-one"
            )
            self.assertEqual(data["conversations"][0]["status"], "running")
            checkpoint = data["checkpoints"][0]
            self.assertEqual(checkpoint["turn_label"], "Turn 3")
            self.assertEqual(checkpoint["turn_id"], "turn-003")
            self.assertEqual(checkpoint["gate"], "stop")
            self.assertEqual(checkpoint["map"][0]["code_id"], "MAST-1")
            limited = monitor_snapshot(
                root,
                workspace.root,
                BASE_STORE,
                project_id="demo-a1b2",
                conversation_id="conversation-one",
                window="count",
                limit=1,
            )
            self.assertEqual(len(limited["checkpoints"]), 1)
            self.assertEqual(
                limited["checkpoints"][0]["checkpoint_id"],
                "cp-clean-later",
            )
            platform_only = monitor_snapshot(
                root,
                workspace.root,
                BASE_STORE,
                project_id="demo-a1b2",
                task_group="platform",
            )
            self.assertEqual(
                platform_only["selection"]["task_group_filter"], "platform"
            )
            self.assertEqual(
                [item["conversation_id"] for item in platform_only["conversations"]],
                ["conversation-platform"],
            )


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store_dir = self.root / "taxonomies"
        copy_store(self.store_dir)
        self.workspace = ProgramWorkspace(self.root / "program")
        self.workspace.bind_inherited_taxonomy(BASE_ID)
        self.server = build_server(
            self.workspace.root,
            self.store_dir,
            port=0,
            monitor_root=self.root,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temp.cleanup()

    def _get(self, path: str) -> tuple[int, str, str]:
        with urlopen(f"http://127.0.0.1:{self.port}{path}") as response:
            return (
                response.status,
                response.headers["Content-Type"],
                response.read().decode("utf-8"),
            )

    def test_page_has_live_controls_and_optional_metric_renderer(self):
        status, content_type, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("AdaMAST Monitor", body)
        self.assertIn("Conversation flight recorder", body)
        self.assertIn("project-select", body)
        self.assertIn("conversation-select", body)
        self.assertIn("Failure modes", body)
        self.assertIn("Checkpoints", body)
        self.assertIn("Past 24h", body)
        self.assertIn("Last N", body)
        self.assertIn("Show clean in failure view", body)
        self.assertNotIn("Advanced filters", body)
        self.assertNotIn('aria-label="Filter by task group"', body)
        self.assertIn("Exact turn ID", body)
        self.assertIn("Not recorded for this checkpoint", body)
        self.assertNotIn("Attached when the turn closes", body)
        self.assertIn("Map / relevant codes", body)
        self.assertIn("Failure modes → checkpoints", body)
        self.assertNotIn("â†’", body)
        self.assertNotIn("TASK-1042", body)

    def test_api_returns_render_ready_full_taxonomy(self):
        status, content_type, body = self._get("/api/taxonomy")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        data = json.loads(body)
        self.assertEqual(data["taxonomy_id"], BASE_ID)
        self.assertEqual(data["repo"], "django/django")
        self.assertEqual(data["codes"][0]["code_id"], "1")
        self.assertIn("N+1 query", data["codes"][0]["name"])
        self.assertIn("queryset", data["codes"][0]["description"])
        self.assertIsNone(data["codes"][0]["fire_count"])
        self.assertEqual(data["codes"][0]["task_firings"], [])
        self.assertEqual(data["codes"][0]["runtime_evidence"], [])
        self.assertEqual(data["clean_checkpoints"], [])

    def test_monitor_api_returns_project_and_conversation_catalog(self):
        state_dir = self.workspace.root / ".adamast-codex"
        state_dir.mkdir()
        (state_dir / "web-conversation.json").write_text(
            json.dumps(
                {
                    "session_id": "web-conversation",
                    "conversation_id": "web-conversation",
                    "conversation_title": "Inspect monitor API",
                    "episode_sequence": 1,
                    "finished": True,
                }
            ),
            encoding="utf-8",
        )
        status, content_type, body = self._get(
            "/api/monitor?conversation=web-conversation"
        )
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        data = json.loads(body)
        self.assertEqual(
            data["selection"]["conversation_id"], "web-conversation"
        )
        self.assertEqual(data["conversations"][0]["title"], "Inspect monitor API")

    def test_api_normalizes_optional_placeholder_metrics(self):
        record = json.loads(
            (self.store_dir / f"{BASE_ID}.json").read_text(encoding="utf-8")
        )
        record["codes"][0]["fire_count"] = 5
        record["codes"][0]["task_firings"] = [
            {"task_id": "TASK-A", "count": 2},
            {"task_id": "TASK-B", "count": 3},
        ]
        (self.store_dir / f"{BASE_ID}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
        _, _, body = self._get("/api/taxonomy")
        first = json.loads(body)["codes"][0]
        self.assertEqual(first["fire_count"], 5)
        self.assertEqual(
            first["task_firings"],
            [
                {"task_id": "TASK-A", "label": "TASK-A", "count": 2},
                {"task_id": "TASK-B", "label": "TASK-B", "count": 3},
            ],
        )

    def test_api_changes_without_server_restart(self):
        _, _, before_body = self._get("/api/taxonomy")
        original = json.loads(
            (self.store_dir / f"{BASE_ID}.json").read_text(encoding="utf-8")
        )
        successor = {**original, "taxonomy_id": NEXT_ID}
        (self.store_dir / f"{NEXT_ID}.json").write_text(
            json.dumps(successor), encoding="utf-8"
        )
        TaxonomyLineage(self.store_dir).add_successor(BASE_ID, NEXT_ID)
        _, _, after_body = self._get("/api/taxonomy")
        self.assertEqual(json.loads(before_body)["taxonomy_id"], BASE_ID)
        self.assertEqual(json.loads(after_body)["taxonomy_id"], NEXT_ID)

    def test_unknown_route_is_404(self):
        with self.assertRaises(HTTPError) as error:
            self._get("/missing")
        try:
            self.assertEqual(error.exception.code, 404)
        finally:
            error.exception.close()


class ManagedDashboardTests(unittest.TestCase):
    def test_ready_timeout_env_override(self):
        with patch.dict(os.environ, {"ADAMAST_DASHBOARD_TIMEOUT": "42.5"}):
            self.assertEqual(_ready_timeout(), 42.5)
        with patch.dict(os.environ, {"ADAMAST_DASHBOARD_TIMEOUT": "0"}):
            self.assertEqual(_ready_timeout(), 1.0)
        with patch.dict(os.environ, {"ADAMAST_DASHBOARD_TIMEOUT": "bogus"}):
            self.assertEqual(_ready_timeout(), DEFAULT_READY_TIMEOUT)
        with patch.dict(os.environ, {"ADAMAST_DASHBOARD_TIMEOUT": ""}):
            self.assertEqual(_ready_timeout(), DEFAULT_READY_TIMEOUT)

    def test_start_reuse_and_stop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = ProgramWorkspace(root / "program", repo="owner/project")
            with patch.dict(
                os.environ,
                {"ADAMAST_DISABLE_DASHBOARD": ""},
            ):
                first = ensure_dashboard(workspace, BASE_STORE, timeout=30.0)
                self.assertTrue(first)
                second = ensure_dashboard(workspace, BASE_STORE)
                self.assertEqual(second, first)
                with urlopen(first) as response:
                    page = response.read().decode("utf-8")
                self.assertIn("AdaMAST / runtime failure modes", page)
                self.assertNotIn("Conversation flight recorder", page)
                with urlopen(f"{first}api/health") as response:
                    health = json.loads(response.read().decode("utf-8"))
                self.assertEqual(health["program_id"], workspace.program_id)
                self.assertEqual(health["view"], "taxonomy")
                self.assertTrue(stop_dashboard(workspace))


if __name__ == "__main__":
    unittest.main()
