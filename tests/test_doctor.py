"""Health-check CLI tests."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adamast.hosts.codex.config import CodexConfig
from adamast.hosts.claude_code.config import ClaudeCodeConfig
from adamast.doctor import ERROR, WARN, has_errors, run_checks


class DoctorCheckTests(unittest.TestCase):
    def test_basic_checks_pass_with_temp_storage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            checks = run_checks(
                store_dir=root / "taxonomies",
                trace_root=root / "traces",
                trace_output=root / "program",
                adamast_model="gpt-5",
            )
        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["python"].status, "ok")
        self.assertEqual(by_name["taxonomy store"].status, "ok")
        self.assertEqual(by_name["trace root"].status, "ok")
        self.assertEqual(by_name["trace output"].status, "ok")
        self.assertEqual(by_name["adamast model"].status, "ok")
        self.assertFalse(has_errors(checks))

    def test_missing_model_is_warning_not_error(self):
        with tempfile.TemporaryDirectory() as td:
            checks = run_checks(
                store_dir=Path(td) / "taxonomies",
                trace_root=Path(td) / "traces",
            )
        self.assertEqual(
            [check.status for check in checks if check.name == "adamast model"],
            [WARN],
        )
        self.assertFalse(has_errors(checks))

    def test_unrecognized_model_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            checks = run_checks(
                store_dir=Path(td) / "taxonomies",
                trace_root=Path(td) / "traces",
                adamast_model="totally-not-a-real-model",
            )
        self.assertEqual(
            [check.status for check in checks if check.name == "adamast model"],
            [ERROR],
        )
        self.assertTrue(has_errors(checks))

    def test_json_cli_exits_zero_for_warnings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "adamast.doctor",
                    "--store-dir",
                    str(root / "taxonomies"),
                    "--trace-root",
                    str(root / "traces"),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(any(item["name"] == "adamast model" for item in payload))

    def test_invalid_dashboard_port_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "adamast.doctor",
                    "--store-dir",
                    str(root / "taxonomies"),
                    "--trace-root",
                    str(root / "traces"),
                    "--dashboard-port",
                    "70000",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("dashboard port", proc.stdout)

    def test_dashboard_port_zero_means_ephemeral_port_check(self):
        with tempfile.TemporaryDirectory() as td:
            checks = run_checks(
                store_dir=Path(td) / "taxonomies",
                trace_root=Path(td) / "traces",
                dashboard_port=0,
            )
        dashboard = [check for check in checks if check.name == "dashboard port"]
        self.assertEqual(dashboard[0].status, "ok")

    def test_codex_checks_warn_when_cli_missing_but_do_not_error(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch("adamast.doctor.shutil.which", return_value=None),
            patch("adamast.doctor.Path.home", return_value=Path(td)),
            patch("adamast.doctor.Path.cwd", return_value=Path(td)),
        ):
            checks = run_checks(
                store_dir=Path(td) / "taxonomies",
                trace_root=Path(td) / "traces",
                codex=True,
            )
        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["codex cli"].status, WARN)
        self.assertEqual(by_name["codex hooks"].status, "ok")
        self.assertFalse(has_errors(checks))

    def test_codex_cli_ok_when_version_command_succeeds(self):
        completed = subprocess.CompletedProcess(
            args=["codex", "--version"],
            returncode=0,
            stdout="codex 1.2.3\n",
            stderr="",
        )
        with (
            tempfile.TemporaryDirectory() as td,
            patch("adamast.doctor.shutil.which", return_value="codex"),
            patch("adamast.doctor.subprocess.run", return_value=completed),
            patch("adamast.doctor.Path.home", return_value=Path(td)),
            patch("adamast.doctor.Path.cwd", return_value=Path(td)),
        ):
            checks = run_checks(
                store_dir=Path(td) / "taxonomies",
                trace_root=Path(td) / "traces",
                codex=True,
            )
        self.assertEqual(
            [check for check in checks if check.name == "codex cli"][0].status,
            "ok",
        )

    def test_native_codex_config_uses_the_active_task_without_a_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / ".codex"
            config_dir.mkdir()
            config = CodexConfig(
                trace_output=root / ".adamast" / "interactive",
                adamast_model="interactive-session",
                project_scope="auto",
                session_selector="prompt",
                learning_backend="codex_subagent",
            )
            (config_dir / "adamast.json").write_text(
                json.dumps(config.to_dict()),
                encoding="utf-8",
            )
            with (
                patch("adamast.doctor.Path.cwd", return_value=root / "project"),
                patch("adamast.doctor.Path.home", return_value=root),
                patch("adamast.doctor.shutil.which", return_value=None),
            ):
                checks = run_checks(
                    store_dir=root / "taxonomies",
                    trace_root=root / "traces",
                    codex=True,
                )

        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["codex config"].status, "ok")
        self.assertEqual(by_name["codex cli"].status, "ok")
        self.assertEqual(by_name["codex auth"].status, "ok")
        self.assertFalse(has_errors(checks))

    def test_native_claude_config_uses_active_session_without_auth_probe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / ".claude"
            config_dir.mkdir()
            config = ClaudeCodeConfig(
                trace_output=root / ".adamast" / "interactive",
                adamast_model="interactive-session",
                project_scope="auto",
                session_selector="prompt",
                selector_surface="browser",
                learning_backend="claude_subagent",
            )
            (config_dir / "adamast.json").write_text(
                json.dumps(config.to_dict()),
                encoding="utf-8",
            )
            with (
                patch("adamast.doctor.Path.cwd", return_value=root / "project"),
                patch("adamast.doctor.Path.home", return_value=root),
                patch(
                    "adamast.hosts.claude_code.install.verify_installed_hooks",
                    return_value="2.1.185",
                ),
                patch(
                    "adamast.doctor._native_auth_check",
                    side_effect=AssertionError("standalone auth must not be probed"),
                ),
            ):
                checks = run_checks(
                    store_dir=root / "taxonomies",
                    trace_root=root / "traces",
                    claude_code=True,
                )

        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["claude code"].status, "ok")
        self.assertEqual(by_name["claude config"].status, "ok")
        self.assertEqual(by_name["claude auth"].status, "ok")
        self.assertFalse(has_errors(checks))


if __name__ == "__main__":
    unittest.main()
