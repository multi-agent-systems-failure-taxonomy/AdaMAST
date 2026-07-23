"""Tests for the user-declared custom-hook surface.

Covers: CustomHookSpec validation + config round-trip, installer wires
custom hooks into settings.local.json with the right --custom command,
dispatcher routes via --custom, custom_blocking_checkpoint runs the
same prompt-then-accept loop as the built-in gates, custom_advisory
emits a non-blocking nudge, and the manage_hooks add/remove/list CLIs.
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adamast.hosts.claude_code.config import (
    BUILT_IN_HOOK_EVENTS,
    CLAUDE_CODE_EVENTS,
    CUSTOM_HOOK_CHECKPOINT_KEYS,
    BuiltInHookSpec,
    ClaudeCodeConfig,
    CustomHookSpec,
    parse_built_in_hooks,
)
from adamast.hosts.claude_code.custom import (
    custom_advisory,
    custom_blocking_checkpoint,
)
from adamast.hosts.claude_code.dispatcher import main as dispatcher_main
from adamast.hosts.claude_code.install import install
from adamast.hosts.claude_code.manage_hooks import (
    add_hook,
    add_main,
    list_hooks,
    remove_hook,
    remove_main,
)
from adamast.hosts.claude_code.state import load_state

ROOT = Path(__file__).resolve().parent.parent
STORE_DIR = ROOT / "tests" / "fixtures" / "taxonomies"


def append_assistant(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": text}]},
                }
            )
            + "\n"
        )


def checkpoint_id_from(prompt: str) -> str:
    match = re.search(r"Checkpoint ID:\s*(\S+)", prompt)
    assert match, prompt
    return match.group(1)


def fired_reflection(cid: str, code: str = "MAST-12") -> str:
    return (
        f"AdaMAST reflection:\n"
        f"- Checkpoint ID: {cid}\n"
        f"- Observe: pre-tool inspection.\n"
        f"- Map:\n"
        f"  - {code} | exhibited | evidence: \"missing test step\"\n"
        f"- Correlate: this would trip the gate.\n"
        f"- Decide: change: insert the missing step before the call\n"
    )


class CustomHookSpecValidationTests(unittest.TestCase):
    def test_blocking_default_with_matcher(self):
        spec = CustomHookSpec(
            name="pre-bash", event="PreToolUse", matcher="Bash",
        )
        self.assertEqual(spec.mode, "blocking")
        self.assertEqual(spec.matcher, "Bash")

    def test_unknown_event_rejected(self):
        with self.assertRaises(ValueError):
            CustomHookSpec(name="x", event="MadeUpEvent")

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            CustomHookSpec(name="x", event="PreToolUse", mode="weird")

    def test_invalid_name_rejected(self):
        with self.assertRaises(ValueError):
            CustomHookSpec(name="has spaces", event="PreToolUse")

    def test_empty_matcher_string_rejected(self):
        with self.assertRaises(ValueError):
            CustomHookSpec(name="x", event="PreToolUse", matcher="")

    def test_invalid_command_pattern_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid regex"):
            CustomHookSpec(
                name="x", event="PreToolUse", command_pattern="[",
            )

    def test_unknown_checkpoint_key_rejected(self):
        with self.assertRaises(ValueError):
            CustomHookSpec(
                name="x", event="PreToolUse", checkpoint_key="random",
            )

    def test_all_advertised_events_are_accepted(self):
        for event in CLAUDE_CODE_EVENTS:
            CustomHookSpec(name=f"on_{event.lower()}", event=event)

    def test_all_checkpoint_key_modes_are_accepted(self):
        for key in CUSTOM_HOOK_CHECKPOINT_KEYS:
            CustomHookSpec(
                name=f"k_{key}", event="PreToolUse", checkpoint_key=key,
            )


class BuiltInHookSpecValidationTests(unittest.TestCase):
    def test_defaults_cover_all_built_in_events(self):
        specs = parse_built_in_hooks()
        self.assertEqual(
            tuple(spec.event for spec in specs),
            BUILT_IN_HOOK_EVENTS,
        )
        self.assertEqual(
            next(s for s in specs if s.event == "PostToolUse").matchers,
            ("*",),
        )

    def test_matcher_on_non_tool_result_event_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not support matchers"):
            BuiltInHookSpec(event="SubagentStop", matchers=("Bash",))

    def test_object_form_overrides_enabled_and_matchers(self):
        specs = {
            spec.event: spec
            for spec in parse_built_in_hooks({
                "SubagentStop": False,
                "PostToolUse": ["Bash", "Edit"],
                "PostToolUseFailure": {
                    "enabled": True,
                    "matchers": ["Bash"],
                },
            })
        }
        self.assertFalse(specs["SubagentStop"].enabled)
        self.assertEqual(specs["PostToolUse"].matchers, ("Bash", "Edit"))
        self.assertEqual(specs["PostToolUseFailure"].matchers, ("Bash",))


class ConfigRoundTripTests(unittest.TestCase):
    def test_custom_hooks_round_trip_through_to_dict_load(self):
        hooks = (
            CustomHookSpec(
                name="pre-bash", event="PreToolUse",
                mode="blocking", matcher="Bash",
                command_pattern="python .*eval.py",
                checkpoint_key="fixed",
            ),
            CustomHookSpec(
                name="on-prompt", event="UserPromptSubmit",
                mode="advisory",
            ),
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="gpt-5",
                store_dir=STORE_DIR,
                custom_hooks=hooks,
            )
            path = root / "adamast.json"
            path.write_text(
                json.dumps(cfg.to_dict()), encoding="utf-8",
            )
            loaded = ClaudeCodeConfig.load(path)
            self.assertEqual(loaded.custom_hooks, hooks)

    def test_built_in_hooks_round_trip_through_to_dict_load(self):
        hooks = parse_built_in_hooks({
            "SubagentStop": False,
            "PostToolUse": ["Bash", "Edit"],
        })
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="gpt-5",
                store_dir=STORE_DIR,
                built_in_hooks=hooks,
            )
            path = root / "adamast.json"
            path.write_text(json.dumps(cfg.to_dict()), encoding="utf-8")
            loaded = ClaudeCodeConfig.load(path)
            self.assertEqual(loaded.built_in_hooks, hooks)

    def test_legacy_config_without_custom_hooks_still_loads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "adamast.json"
            path.write_text(
                json.dumps({
                    "trace_output": str(root / "program"),
                    "adamast_model": "gpt-5",
                }),
                encoding="utf-8",
            )
            loaded = ClaudeCodeConfig.load(path)
            self.assertEqual(loaded.custom_hooks, ())

    def test_duplicate_names_in_constructor_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate custom_hook"):
            ClaudeCodeConfig(
                trace_output=Path("/tmp/x"),
                adamast_model="m",
                custom_hooks=(
                    CustomHookSpec(name="a", event="PreToolUse"),
                    CustomHookSpec(name="a", event="UserPromptSubmit"),
                ),
            )


class InstallerCustomHookTests(unittest.TestCase):
    def test_install_writes_matcher_and_custom_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
                custom_hooks=(
                    CustomHookSpec(
                        name="pre-bash", event="PreToolUse",
                        matcher="Bash",
                    ),
                ),
            )
            info = install(root, cfg, verify=False)
            settings = json.loads(
                Path(info["settings"]).read_text(encoding="utf-8")
            )
            self.assertIn("PreToolUse", settings["hooks"])
            entries = settings["hooks"]["PreToolUse"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["matcher"], "Bash")
            command = entries[0]["hooks"][0]["command"]
            self.assertIn("--custom", command)
            self.assertIn("pre-bash", command)

    def test_install_is_idempotent_for_custom_hooks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
                custom_hooks=(
                    CustomHookSpec(
                        name="pre-bash", event="PreToolUse",
                        matcher="Bash",
                    ),
                ),
            )
            install(root, cfg, verify=False)
            install(root, cfg, verify=False)
            settings = json.loads(
                (root / ".claude" / "settings.local.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(len(settings["hooks"]["PreToolUse"]), 1)

    def test_two_custom_hooks_on_same_event_get_separate_entries(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
                custom_hooks=(
                    CustomHookSpec(
                        name="bash-gate", event="PreToolUse",
                        matcher="Bash",
                    ),
                    CustomHookSpec(
                        name="edit-gate", event="PreToolUse",
                        matcher="Edit",
                    ),
                ),
            )
            info = install(root, cfg, verify=False)
            settings = json.loads(
                Path(info["settings"]).read_text(encoding="utf-8")
            )
            entries = settings["hooks"]["PreToolUse"]
            self.assertEqual(len(entries), 2)
            self.assertEqual(
                {e["matcher"] for e in entries}, {"Bash", "Edit"},
            )

    def test_uninstall_cleans_custom_hooks_too(self):
        from adamast.hosts.claude_code.uninstall import uninstall

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
                custom_hooks=(
                    CustomHookSpec(
                        name="pre-bash", event="PreToolUse",
                        matcher="Bash",
                    ),
                ),
            )
            install(root, cfg, verify=False)
            uninstall(root)
            settings_path = root / ".claude" / "settings.local.json"
            remaining = json.loads(
                settings_path.read_text(encoding="utf-8")
            )
            self.assertNotIn("PreToolUse", remaining.get("hooks", {}))


class CustomBlockingCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trace_output = self.root / "program"
        self.transcript = self.root / "transcript.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        self.spec = CustomHookSpec(
            name="pre-bash", event="PreToolUse", matcher="Bash",
        )
        self.config = ClaudeCodeConfig(
            trace_output=self.trace_output,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            custom_hooks=(self.spec,),
            max_retries=2,
        )
        with patch.dict(os.environ, {"ADAMAST_DISABLE_DASHBOARD": "1"}):
            from adamast.hosts.claude_code.hooks import session_start
            session_start.handle(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "sess-1",
                    "transcript_path": str(self.transcript),
                    "cwd": str(self.root),
                },
                self.config,
            )

    def tearDown(self):
        self.temp.cleanup()

    def _event(self, **overrides) -> dict:
        base = {
            "hook_event_name": "PreToolUse",
            "session_id": "sess-1",
            "transcript_path": str(self.transcript),
            "cwd": str(self.root),
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
            "tool_use_id": "toolu_abc",
        }
        base.update(overrides)
        return base

    def test_first_fire_blocks_with_reflection_prompt(self):
        code, prompt = custom_blocking_checkpoint(
            self._event(), self.config, spec=self.spec,
        )
        self.assertEqual(code, 2)
        self.assertIn("Checkpoint ID:", prompt)
        self.assertIn("custom hook: pre-bash", prompt)
        # Custom hooks must NOT carry submission-gate language.
        self.assertNotIn("Final AdaMAST status:", prompt)
        self.assertNotIn("READY_TO_SUBMIT", prompt)

    def test_valid_reflection_unblocks_and_records(self):
        code, prompt = custom_blocking_checkpoint(
            self._event(), self.config, spec=self.spec,
        )
        cid = checkpoint_id_from(prompt)
        append_assistant(self.transcript, fired_reflection(cid))
        code, message = custom_blocking_checkpoint(
            self._event(), self.config, spec=self.spec,
        )
        self.assertEqual(code, 0)
        self.assertIn("accepted", message)
        # Evidence file gained a custom: gate firing.
        evidence_path = self.trace_output / ".adamast-runtime-evidence.json"
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
        firings = data["taxonomies"]["mast"]["codes"]["MAST-12"]["events"]
        self.assertTrue(
            any(e["gate"] == "custom:pre-bash" for e in firings),
            "fired event must be tagged with the custom gate name",
        )

    def test_retry_guard_releases_after_max_failures(self):
        _, prompt = custom_blocking_checkpoint(
            self._event(), self.config, spec=self.spec,
        )
        cid = checkpoint_id_from(prompt)
        # max_retries=2 → first failure re-blocks, second hits the limit.
        append_assistant(
            self.transcript,
            f"AdaMAST reflection:\n- Checkpoint ID: {cid}\n- Observe: hollow",
        )
        self.assertEqual(
            custom_blocking_checkpoint(
                self._event(), self.config, spec=self.spec,
            )[0],
            2,
        )
        code, message = custom_blocking_checkpoint(
            self._event(), self.config, spec=self.spec,
        )
        self.assertEqual(code, 0)
        self.assertIn("hook-owned retry limit", message)

    def test_distinct_tool_use_ids_make_distinct_checkpoints(self):
        first_event = self._event(tool_use_id="toolu_aaa")
        code_a, prompt_a = custom_blocking_checkpoint(
            first_event, self.config, spec=self.spec,
        )
        second_event = self._event(tool_use_id="toolu_bbb")
        code_b, prompt_b = custom_blocking_checkpoint(
            second_event, self.config, spec=self.spec,
        )
        # Both block independently; they cannot share the same checkpoint id.
        self.assertEqual(code_a, 2)
        self.assertEqual(code_b, 2)
        self.assertNotEqual(
            checkpoint_id_from(prompt_a),
            checkpoint_id_from(prompt_b),
        )
        state = load_state(self.trace_output, "sess-1")
        keys = list(state["pending"].keys())
        self.assertEqual(
            len([k for k in keys if k.startswith("custom:pre-bash:")]), 2,
        )

    def test_fixed_checkpoint_key_reuses_pending_gate(self):
        spec = CustomHookSpec(
            name="pre-bash", event="PreToolUse", matcher="Bash",
            checkpoint_key="fixed",
        )
        config = ClaudeCodeConfig(
            trace_output=self.trace_output,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            custom_hooks=(spec,),
            max_retries=2,
        )
        code_a, prompt_a = custom_blocking_checkpoint(
            self._event(tool_use_id="toolu_aaa"), config, spec=spec,
        )
        code_b, prompt_b = custom_blocking_checkpoint(
            self._event(tool_use_id="toolu_bbb"), config, spec=spec,
        )
        self.assertEqual(code_a, 2)
        self.assertEqual(code_b, 2)
        self.assertEqual(checkpoint_id_from(prompt_a), checkpoint_id_from(prompt_b))
        state = load_state(self.trace_output, "sess-1")
        self.assertIn("custom:pre-bash:fixed", state["pending"])

    def test_command_pattern_skips_non_matching_tool_input(self):
        spec = CustomHookSpec(
            name="eval-only", event="PreToolUse", matcher="Bash",
            command_pattern=r"python\s+eval\.py",
        )
        config = ClaudeCodeConfig(
            trace_output=self.trace_output,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            custom_hooks=(spec,),
            max_retries=2,
        )
        code, output = custom_blocking_checkpoint(
            self._event(tool_input={"command": "python train.py"}),
            config,
            spec=spec,
        )
        self.assertEqual(code, 0)
        self.assertEqual(output, "")
        state = load_state(self.trace_output, "sess-1")
        self.assertFalse(state.get("pending"))

        code, prompt = custom_blocking_checkpoint(
            self._event(tool_input={"command": "python eval.py --smoke"}),
            config,
            spec=spec,
        )
        self.assertEqual(code, 2)
        self.assertIn("custom hook: eval-only", prompt)


class CustomAdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trace_output = self.root / "program"
        self.transcript = self.root / "transcript.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        self.spec = CustomHookSpec(
            name="on-prompt", event="UserPromptSubmit", mode="advisory",
        )
        self.config = ClaudeCodeConfig(
            trace_output=self.trace_output,
            adamast_model="test-model",
            store_dir=STORE_DIR,
            custom_hooks=(self.spec,),
        )
        with patch.dict(os.environ, {"ADAMAST_DISABLE_DASHBOARD": "1"}):
            from adamast.hosts.claude_code.hooks import session_start
            session_start.handle(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "adv-1",
                    "transcript_path": str(self.transcript),
                    "cwd": str(self.root),
                },
                self.config,
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_emits_additional_context_envelope(self):
        out = custom_advisory(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "adv-1",
                "transcript_path": str(self.transcript),
                "cwd": str(self.root),
                "prompt": "ship the patch now",
            },
            self.config,
            spec=self.spec,
        )
        self.assertEqual(
            out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit",
        )
        body = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("custom hook nudge: on-prompt", body)
        self.assertIn("advisory; non-blocking", body)


class DispatcherRoutingTests(unittest.TestCase):
    def _run_dispatcher(self, payload, *, custom: str, config_path: Path):
        argv = ["--config", str(config_path), "--custom", custom]
        with (
            patch("sys.stdin", io.StringIO(json.dumps(payload))),
            patch("sys.stdout", new_callable=io.StringIO),
            patch("sys.stderr", new_callable=io.StringIO),
        ):
            return dispatcher_main(argv)

    def test_dispatcher_routes_pretooluse_through_custom_hook(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "t.jsonl"
            transcript.write_text("", encoding="utf-8")
            cfg = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
                custom_hooks=(
                    CustomHookSpec(
                        name="pre-bash", event="PreToolUse",
                        matcher="Bash",
                    ),
                ),
            )
            config_path = root / "adamast.json"
            config_path.write_text(json.dumps(cfg.to_dict()), encoding="utf-8")
            with patch.dict(os.environ, {"ADAMAST_DISABLE_DASHBOARD": "1"}):
                code = self._run_dispatcher(
                    {
                        "hook_event_name": "PreToolUse",
                        "session_id": "disp-1",
                        "transcript_path": str(transcript),
                        "cwd": str(root),
                        "tool_name": "Bash",
                        "tool_input": {"command": "ls"},
                        "tool_use_id": "toolu_x",
                    },
                    custom="pre-bash",
                    config_path=config_path,
                )
            # Blocking custom hook returns exit code 2 on first fire.
            self.assertEqual(code, 2)

    def test_dispatcher_errors_on_unknown_custom_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = ClaudeCodeConfig(
                trace_output=root / "program",
                adamast_model="test-model",
                store_dir=STORE_DIR,
            )
            config_path = root / "adamast.json"
            config_path.write_text(json.dumps(cfg.to_dict()), encoding="utf-8")
            code = self._run_dispatcher(
                {"hook_event_name": "PreToolUse", "session_id": "x"},
                custom="ghost",
                config_path=config_path,
            )
            self.assertEqual(code, 1)


class ManageHooksCliTests(unittest.TestCase):
    def _bootstrap(self, root: Path):
        cfg = ClaudeCodeConfig(
            trace_output=root / "program",
            adamast_model="test-model",
            store_dir=STORE_DIR,
        )
        install(root, cfg, verify=False)

    def test_add_remove_list_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._bootstrap(root)

            result = add_hook(
                root,
                CustomHookSpec(
                    name="pre-bash", event="PreToolUse", matcher="Bash",
                ),
            )
            self.assertEqual(result["custom_hook"]["name"], "pre-bash")
            self.assertEqual(
                [h["name"] for h in list_hooks(root)], ["pre-bash"],
            )
            hook = list_hooks(root)[0]
            self.assertEqual(hook["checkpoint_key"], "tool_use_id")
            self.assertIsNone(hook["command_pattern"])

            # Re-adding without overwrite fails.
            with self.assertRaisesRegex(ValueError, "already exists"):
                add_hook(
                    root,
                    CustomHookSpec(
                        name="pre-bash", event="PreToolUse",
                        matcher="Edit",
                    ),
                )

            # Overwrite path replaces in place.
            add_hook(
                root,
                CustomHookSpec(
                    name="pre-bash", event="PreToolUse", matcher="Edit",
                    command_pattern="Write",
                    checkpoint_key="command",
                ),
                overwrite=True,
            )
            hook = list_hooks(root)[0]
            self.assertEqual(hook["matcher"], "Edit")
            self.assertEqual(hook["command_pattern"], "Write")
            self.assertEqual(hook["checkpoint_key"], "command")

            remove_hook(root, "pre-bash")
            self.assertEqual(list_hooks(root), [])

    def test_add_main_cli_writes_to_disk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._bootstrap(root)
            with patch("sys.stdout", new_callable=io.StringIO):
                rc = add_main([
                    "--project-dir", str(root),
                    "--name", "pre-write",
                    "--event", "PreToolUse",
                    "--matcher", "Write",
                    "--command-pattern", "python .*eval",
                    "--checkpoint-key", "fixed",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(
                list_hooks(root),
                [{
                    "name": "pre-write", "event": "PreToolUse",
                    "mode": "blocking", "matcher": "Write",
                    "command_pattern": "python .*eval",
                    "checkpoint_key": "fixed",
                }],
            )

    def test_remove_main_unknown_name_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._bootstrap(root)
            with patch("sys.stderr", new_callable=io.StringIO):
                rc = remove_main([
                    "--project-dir", str(root), "--name", "ghost",
                ])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
