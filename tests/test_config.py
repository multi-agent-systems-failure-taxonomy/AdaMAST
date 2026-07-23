"""Shared adamast.json configuration tests."""

from __future__ import annotations

import json
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from adamast.core.config import ALL_FIELDS, config_value, load_adamast_config


class AdaMASTConfigTests(unittest.TestCase):
    def test_published_schema_matches_loader_fields(self):
        schema_path = files("adamast.core").joinpath("assets").joinpath("adamast_config.schema.json")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]) - {"version"}, ALL_FIELDS)

    def test_loads_and_normalizes_relative_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({
                    "version": 1,
                    "trace_output": "program",
                    "trace_root": "traces",
                    "store_dir": "taxonomies",
                    "evidence_export": "exports",
                    "adamast_model": "gpt-5",
                    "dashboard": False,
                }),
                encoding="utf-8",
            )
            config = load_adamast_config(config_path)
        self.assertEqual(config["trace_output"], (root / "program").resolve())
        self.assertEqual(config["trace_root"], (root / "traces").resolve())
        self.assertEqual(config["store_dir"], (root / "taxonomies").resolve())
        self.assertEqual(config["evidence_export"], (root / "exports").resolve())
        self.assertEqual(config["adamast_model"], "gpt-5")
        self.assertFalse(config["dashboard"])

    def test_loads_windows_utf8_bom_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({
                    "trace_output": "program",
                    "trace_root": "traces",
                    "store_dir": "taxonomies",
                    "adamast_model": "gpt-5",
                }),
                encoding="utf-8-sig",
            )
            config = load_adamast_config(config_path)
        self.assertEqual(config["trace_output"], (root / "program").resolve())
        self.assertEqual(config["adamast_model"], "gpt-5")

    def test_unknown_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "adamast.json"
            path.write_text(json.dumps({"trace_outpt": "typo"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown AdaMAST config field"):
                load_adamast_config(path)

    def test_explicit_cli_value_wins_over_config(self):
        args = SimpleNamespace(trace_output=Path("cli-program"))
        config = {"trace_output": Path("config-program")}
        self.assertEqual(config_value(args, config, "trace_output"), Path("cli-program"))

    def test_doctor_cli_reads_config_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({
                    "trace_output": "program",
                    "trace_root": "traces",
                    "store_dir": "taxonomies",
                    "adamast_model": "gpt-5",
                }),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "adamast.doctor",
                    "--config",
                    str(config_path),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(any(item["name"] == "trace output" for item in payload))
        self.assertTrue(any(item["name"] == "adamast model" for item in payload))

    def test_traces_status_cli_reads_trace_root_from_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({"trace_root": "traces"}),
                encoding="utf-8",
            )
            (root / "traces" / "tax-alpha").mkdir(parents=True)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "adamast.core.traces_cli",
                    "status",
                    "--config",
                    str(config_path),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload[0]["collection"], "tax-alpha")

    def test_single_llm_cli_can_take_required_values_from_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({
                    "model": "gpt-5",
                    "adamast_model": "gpt-5",
                    "trace_output": "program",
                    "trace_root": "traces",
                    "dashboard": False,
                }),
                encoding="utf-8",
            )
            fake_result = SimpleNamespace(answer="done")
            with (
                patch(
                    "adamast.hosts.single_llm.cli.provider_call",
                    return_value=lambda _messages: "done",
                ),
                patch(
                    "adamast.hosts.single_llm.cli.run_single_llm",
                    return_value=fake_result,
                ) as run,
            ):
                from adamast.hosts.single_llm.cli import main

                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = main(["--config", str(config_path), "--task", "do it"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[2].trace_output, (root / "program").resolve())

    def test_claude_install_cli_can_take_required_values_from_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({
                    "project_dir": "project",
                    "adamast_model": "gpt-5",
                    "trace_output": "program",
                    "store_dir": "taxonomies",
                    "dashboard": False,
                    "generation_threshold": 9,
                    "built_in_hooks": {
                        "SubagentStop": False,
                        "PostToolUse": ["Bash", "Edit"],
                    },
                }),
                encoding="utf-8",
            )
            captured = {}

            def fake_install(project_dir, config, **_kwargs):
                captured["project_dir"] = Path(project_dir)
                captured["config"] = config
                return {}

            with patch(
                "adamast.hosts.claude_code.install.install",
                side_effect=fake_install,
            ):
                from adamast.hosts.claude_code.install import main

                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = main(["--config", str(config_path)])
        self.assertEqual(code, 0)
        self.assertEqual(captured["project_dir"], (root / "project").resolve())
        self.assertEqual(captured["config"].trace_output, (root / "program").resolve())
        self.assertEqual(captured["config"].generation_threshold, 9)
        self.assertFalse(captured["config"].dashboard)
        hooks = {
            spec.event: spec for spec in captured["config"].built_in_hooks
        }
        self.assertFalse(hooks["SubagentStop"].enabled)
        self.assertEqual(hooks["PostToolUse"].matchers, ("Bash", "Edit"))

    def test_claude_install_cli_prefers_scoped_adapter_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({
                    "project_dir": "project",
                    "adamast_model": "gpt-5",
                    "trace_output": "program",
                    "store_dir": "taxonomies",
                    "built_in_hooks": {"SubagentStop": True},
                    "claude_code": {
                        "built_in_hooks": {"SubagentStop": False},
                        "custom_hooks": [{
                            "name": "eval-only",
                            "event": "PreToolUse",
                            "matcher": "Bash",
                            "command_pattern": "python .*eval",
                            "checkpoint_key": "fixed",
                        }],
                    },
                }),
                encoding="utf-8",
            )
            captured = {}

            def fake_install(project_dir, config, **_kwargs):
                captured["project_dir"] = Path(project_dir)
                captured["config"] = config
                return {}

            with patch(
                "adamast.hosts.claude_code.install.install",
                side_effect=fake_install,
            ):
                from adamast.hosts.claude_code.install import main

                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = main(["--config", str(config_path)])
        self.assertEqual(code, 0)
        hooks = {spec.event: spec for spec in captured["config"].built_in_hooks}
        self.assertFalse(hooks["SubagentStop"].enabled)
        self.assertEqual(captured["config"].custom_hooks[0].name, "eval-only")
        self.assertEqual(captured["config"].custom_hooks[0].checkpoint_key, "fixed")

    def test_codex_install_cli_can_take_required_values_from_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({
                    "project_dir": "project",
                    "adamast_model": "gpt-5",
                    "trace_output": "program",
                    "store_dir": "taxonomies",
                    "dashboard": False,
                    "generation_threshold": 7,
                    "codex_hooks": {
                        "SubagentStop": False,
                        "PostToolUse": ["Bash", "Edit|Write"],
                    },
                }),
                encoding="utf-8",
            )
            captured = {}

            def fake_install(project_dir, config, **_kwargs):
                captured["project_dir"] = Path(project_dir)
                captured["config"] = config
                return {}

            with patch(
                "adamast.hosts.codex.install.install",
                side_effect=fake_install,
            ):
                from adamast.hosts.codex.install import main

                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = main(["--config", str(config_path)])
        self.assertEqual(code, 0)
        self.assertEqual(captured["project_dir"], (root / "project").resolve())
        self.assertEqual(captured["config"].trace_output, (root / "program").resolve())
        self.assertEqual(captured["config"].generation_threshold, 7)
        self.assertFalse(captured["config"].dashboard)
        hooks = {spec.event: spec for spec in captured["config"].hooks}
        self.assertFalse(hooks["SubagentStop"].enabled)
        self.assertEqual(hooks["PostToolUse"].matchers, ("Bash", "Edit|Write"))

    def test_codex_install_cli_prefers_scoped_adapter_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({
                    "project_dir": "project",
                    "adamast_model": "gpt-5",
                    "trace_output": "program",
                    "store_dir": "taxonomies",
                    "codex_hooks": {"SubagentStop": True},
                    "codex": {
                        "hooks": {
                            "SubagentStop": False,
                            "PostToolUse": ["shell_command"],
                        }
                    },
                }),
                encoding="utf-8",
            )
            captured = {}

            def fake_install(project_dir, config, **_kwargs):
                captured["project_dir"] = Path(project_dir)
                captured["config"] = config
                return {}

            with patch(
                "adamast.hosts.codex.install.install",
                side_effect=fake_install,
            ):
                from adamast.hosts.codex.install import main

                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = main(["--config", str(config_path)])
        self.assertEqual(code, 0)
        hooks = {spec.event: spec for spec in captured["config"].hooks}
        self.assertFalse(hooks["SubagentStop"].enabled)
        self.assertEqual(hooks["PostToolUse"].matchers, ("shell_command",))

    def test_import_traces_cli_uses_config_for_model_and_storage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            traces = root / "traces.jsonl"
            traces.write_text(
                json.dumps({
                    "problem_id": "p1",
                    "task": "task",
                    "raw_trajectory": "trajectory",
                    "metadata": {},
                }) + "\n",
                encoding="utf-8",
            )
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({
                    "adamast_model": "gpt-5",
                    "store_dir": "taxonomies",
                    "trace_root": "trace-root",
                }),
                encoding="utf-8",
            )
            fake_result = SimpleNamespace(to_dict=lambda: {"taxonomy_id": "tax-one"})
            with patch(
                "adamast.learning.import_generation.generate_imported_taxonomy",
                return_value=fake_result,
            ) as generate:
                from adamast.learning.import_generation import main

                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = main(["--config", str(config_path), "--traces", str(traces)])
        self.assertEqual(code, 0)
        self.assertEqual(generate.call_args.kwargs["adamast_model"], "gpt-5")
        self.assertEqual(generate.call_args.kwargs["store_dir"], (root / "taxonomies").resolve())
        self.assertEqual(generate.call_args.kwargs["trace_root"], (root / "trace-root").resolve())

    def test_register_taxonomy_cli_uses_config_store_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            taxonomy = root / "taxonomy.json"
            taxonomy.write_text(
                json.dumps({
                    "repo": "",
                    "domain": "demo",
                    "codes": [
                        {
                            "id": "X.1",
                            "name": "Example",
                            "description": "Example failure",
                            "category": "X",
                        }
                    ],
                }),
                encoding="utf-8",
            )
            config_path = root / "adamast.json"
            config_path.write_text(
                json.dumps({"store_dir": "taxonomies"}),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "adamast.learning.register_taxonomy",
                    "--config",
                    str(config_path),
                    "--file",
                    str(taxonomy),
                    "--id",
                    "tax-configured",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["taxonomy_id"], "tax-configured")
        self.assertEqual(
            Path(payload["taxonomy_path"]),
            (root / "taxonomies" / "tax-configured.json").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
