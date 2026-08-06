from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

from adamast.hosts import plugin_dispatcher

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "adamast"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_native_plugin_versions_and_marketplaces_stay_in_sync():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    expected = re.search(
        r'(?ms)^\[project\]\s+.*?^version = "([^"]+)"$',
        pyproject,
    ).group(1)
    claude_manifest = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    codex_manifest = _json(PLUGIN / ".codex-plugin" / "plugin.json")
    claude_marketplace = _json(ROOT / ".claude-plugin" / "marketplace.json")
    codex_marketplace = _json(ROOT / ".agents" / "plugins" / "marketplace.json")

    assert expected == "0.2.2.1"
    assert claude_manifest["version"] == expected
    assert codex_manifest["version"] == expected
    assert claude_marketplace["metadata"]["version"] == expected
    assert claude_marketplace["plugins"][0]["version"] == expected
    assert codex_marketplace["plugins"][0]["source"] == {
        "source": "local",
        "path": "./plugins/adamast",
    }
    assert "hooks" not in codex_manifest
    assert claude_manifest["hooks"] == "./hooks/claude.json"


def test_claude_hooks_use_quoted_cross_platform_plugin_launcher():
    hooks = _json(PLUGIN / "hooks" / "claude.json")["hooks"]
    assert len(hooks) == 8
    for event, registrations in hooks.items():
        hook = registrations[0]["hooks"][0]
        assert hook["command"] == (
            'sh "${CLAUDE_PLUGIN_ROOT}/bin/adamast-hook" '
            f"--host claude --event {event}"
        )
        assert "%CLAUDE_PLUGIN_ROOT%\\bin\\adamast-hook.cmd" in hook["commandWindows"]
        assert f"claude {event}" in hook["commandWindows"]


def test_codex_hooks_have_posix_and_windows_commands():
    hooks = _json(PLUGIN / "hooks" / "hooks.json")["hooks"]
    assert set(hooks) == {
        "SessionStart",
        "UserPromptSubmit",
        "PostToolUse",
        "SubagentStop",
        "Stop",
    }
    for event, registrations in hooks.items():
        hook = registrations[0]["hooks"][0]
        assert hook["command"] == (
            'sh "${PLUGIN_ROOT}/bin/adamast-hook" '
            f"--host codex --event {event}"
        )
        assert "%PLUGIN_ROOT%\\bin\\adamast-hook.cmd" in hook["commandWindows"]
        assert f"codex {event}" in hook["commandWindows"]

    windows_shim = (PLUGIN / "bin" / "adamast-hook.cmd").read_text(
        encoding="utf-8"
    )
    assert 'set "PSModulePath="' in windows_shim
    assert "adamast-hook.ps1" in windows_shim


def test_launchers_do_not_assume_a_unix_uv_tool_location():
    posix = (PLUGIN / "bin" / "adamast-hook").read_text(encoding="utf-8")
    windows = (PLUGIN / "bin" / "adamast-hook.ps1").read_text(encoding="utf-8")

    assert "$HOME/.local/share/uv/tools" not in posix
    assert "Scripts/python.exe" in posix
    assert "bin/python" in posix
    assert "UV_TOOL_DIR" in posix
    assert "UV_TOOL_DIR" in windows
    assert "Scripts\\python.exe" in windows
    assert "adamast==$RUNTIME_VERSION" in posix
    assert '"adamast==$RuntimeVersion"' in windows


@pytest.mark.parametrize(
    ("host", "scoped_key", "backend"),
    [
        ("claude", "claude_code", "claude_subagent"),
        ("codex", "codex", "codex_subagent"),
    ],
)
def test_default_plugin_config_uses_native_paths_and_host_scope(
    tmp_path,
    monkeypatch,
    host,
    scoped_key,
    backend,
):
    config_path = tmp_path / host / "adamast.json"
    traces = tmp_path / "native traces"
    monkeypatch.setenv("ADAMAST_TRACE_OUTPUT", str(traces))

    plugin_dispatcher.write_default_config(host, config_path)

    config = _json(config_path)
    assert Path(config["trace_output"]) == traces.resolve()
    assert config["adamast_model"] == "interactive-session"
    assert config["dashboard"] is True
    assert config["generation_stops"] is False
    assert config["refinement_stops"] is False
    assert config[scoped_key]["project_scope"] == "auto"
    assert config[scoped_key]["session_selector"] == "prompt"
    assert config[scoped_key]["selector_surface"] == "browser"
    assert config[scoped_key]["learning_backend"] == backend


def test_plugin_dispatcher_creates_config_then_replays_event(tmp_path, monkeypatch):
    config_path = tmp_path / "config" / "adamast.json"
    traces = tmp_path / "traces"
    captured = {}

    monkeypatch.setenv("ADAMAST_CONFIG", str(config_path))
    monkeypatch.setenv("ADAMAST_TRACE_OUTPUT", str(traces))
    monkeypatch.setattr(
        plugin_dispatcher,
        "_delegate",
        lambda host, event, config, payload: captured.update(
            host=host,
            event=event,
            config=config,
            payload=json.loads(payload),
        )
        or 17,
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("\ufeff" + json.dumps({"cwd": str(tmp_path)})),
    )

    result = plugin_dispatcher.main(
        ["--host", "codex", "--event", "SessionStart"]
    )

    assert result == 17
    assert config_path.is_file()
    assert captured["host"] == "codex"
    assert captured["event"] == "SessionStart"
    assert captured["config"] == config_path.resolve()
    assert captured["payload"]["hook_event_name"] == "SessionStart"


def test_non_start_event_without_config_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAMAST_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(
        plugin_dispatcher,
        "_delegate",
        lambda *_args, **_kwargs: pytest.fail("dispatcher should not run"),
    )

    assert (
        plugin_dispatcher.main(
            ["--host", "claude", "--event", "PostToolUse"]
        )
        == 0
    )
