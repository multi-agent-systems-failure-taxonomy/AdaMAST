"""The single `adamast` entry point and the grouped installer help."""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

from adamast import cli as umbrella_cli


def run_umbrella(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = umbrella_cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class UmbrellaDispatchTests(unittest.TestCase):
    def test_bare_invocation_prints_grouped_overview(self):
        code, out, _ = run_umbrella([])
        self.assertEqual(code, 0)
        self.assertIn("adamast doctor", out)
        self.assertIn("claude host commands:", out)
        self.assertIn("codex host commands:", out)
        self.assertIn("adamast-<command>", out)

    def test_version_flag(self):
        code, out, _ = run_umbrella(["--version"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("adamast "))

    def test_top_level_command_receives_remaining_argv(self):
        with patch("adamast.doctor.main", return_value=0) as target:
            code, _, _ = run_umbrella(["doctor", "--json"])
        self.assertEqual(code, 0)
        target.assert_called_once_with(["--json"])

    def test_host_command_receives_remaining_argv(self):
        with patch(
            "adamast.hosts.claude_code.install.main", return_value=0
        ) as target:
            code, _, _ = run_umbrella(["claude", "install", "--user-level"])
        self.assertEqual(code, 0)
        target.assert_called_once_with(["--user-level"])

    def test_hook_management_attribute_dispatch(self):
        with patch(
            "adamast.hosts.claude_code.manage_hooks.list_main",
            return_value=0,
        ) as target:
            code, _, _ = run_umbrella(["claude", "list-hooks"])
        self.assertEqual(code, 0)
        target.assert_called_once_with([])

    def test_bare_host_lists_subcommands_and_fails(self):
        code, out, _ = run_umbrella(["claude"])
        self.assertEqual(code, 2)
        self.assertIn("adamast claude install", out)

    def test_host_help_lists_subcommands_and_succeeds(self):
        code, out, _ = run_umbrella(["claude", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("adamast claude checkpoint", out)

    def test_unknown_command_exits_2(self):
        code, _, err = run_umbrella(["bogus"])
        self.assertEqual(code, 2)
        self.assertIn("unknown command 'bogus'", err)

    def test_unknown_host_subcommand_exits_2(self):
        code, _, err = run_umbrella(["codex", "bogus"])
        self.assertEqual(code, 2)
        self.assertIn("unknown command 'bogus'", err)

    def test_every_mapped_target_resolves(self):
        from importlib import import_module

        tables = [umbrella_cli._COMMANDS]
        tables.extend(umbrella_cli._HOST_COMMANDS.values())
        for table in tables:
            for name, (module_path, attribute, _) in table.items():
                with self.subTest(command=name):
                    target = getattr(import_module(module_path), attribute)
                    self.assertTrue(callable(target))


class GroupedInstallerHelpTests(unittest.TestCase):
    def help_text(self, main) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        return out.getvalue()

    def test_claude_install_help_is_grouped_with_quickstart(self):
        from adamast.hosts.claude_code.install import main

        text = self.help_text(main)
        for header in (
            "getting started:",
            "learning behavior:",
            "advanced tuning:",
            "hook selection:",
            "quickstart:",
        ):
            self.assertIn(header, text)

    def test_codex_install_help_is_grouped_with_quickstart(self):
        from adamast.hosts.codex.install import main

        text = self.help_text(main)
        for header in (
            "getting started:",
            "learning behavior:",
            "advanced tuning:",
            "quickstart:",
        ):
            self.assertIn(header, text)


class DoctorGuidanceTests(unittest.TestCase):
    def test_doctor_text_output_ends_with_next_step(self):
        import tempfile
        from pathlib import Path

        from adamast.doctor import main

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "--store-dir",
                        str(root / "store"),
                        "--trace-root",
                        str(root / "traces"),
                    ]
                )
        self.assertEqual(code, 0)
        self.assertIn("Next step:", out.getvalue())
        self.assertIn("--adamast-model", out.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
