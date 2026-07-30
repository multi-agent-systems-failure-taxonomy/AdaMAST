"""Shared entry point for the native Claude Code and Codex plugins.

The plugin launchers are deliberately small platform adapters.  This module
owns path-sensitive work so a shell running under Git Bash never writes POSIX
paths into a Windows configuration file.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path
from typing import Callable

from adamast.hosts.interactive.defaults import (
    INTERACTIVE_ADAMAST_MODEL,
    default_interactive_trace_output,
)
from adamast.hosts.shared import force_utf8_stdio, write_json_atomic

HOSTS = ("claude", "codex")


def main(argv: list[str] | None = None) -> int:
    """Resolve native configuration and delegate one hook event."""
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description="AdaMAST native plugin hook.")
    parser.add_argument("--host", choices=HOSTS, required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)

    # Windows PowerShell 5.1 can prefix piped native-command input with a
    # UTF-8 BOM. JSON permits leading whitespace but Python's decoder rejects
    # that marker unless it is removed explicitly.
    raw_event = sys.stdin.read().lstrip("\ufeff")
    try:
        event = json.loads(raw_event or "{}")
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
        event.setdefault("hook_event_name", args.event)
        config_path = resolve_config_path(
            args.host,
            event,
            explicit=args.config,
        )
        if not config_path.is_file():
            if args.event != "SessionStart":
                return 0
            write_default_config(args.host, config_path)
        return _delegate(
            args.host,
            args.event,
            config_path,
            json.dumps(event, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001 - plugin boundary must report safely
        print(f"AdaMAST {args.host} plugin hook failed: {exc}", file=sys.stderr)
        return 1


def resolve_config_path(
    host: str,
    event: dict,
    *,
    explicit: Path | None = None,
) -> Path:
    """Return the first configured project or user AdaMAST config path."""
    if explicit is not None:
        return explicit.expanduser().resolve()

    configured = os.environ.get("ADAMAST_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()

    candidates: list[Path] = []
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        candidates.append(Path(project_dir) / "adamast.json")
    cwd = event.get("cwd")
    if cwd:
        candidates.append(Path(str(cwd)) / "adamast.json")
    candidates.append(_host_config_dir(host) / "adamast.json")

    for candidate in candidates:
        path = candidate.expanduser().resolve()
        if path.is_file():
            return path
    return candidates[-1].expanduser().resolve()


def write_default_config(host: str, path: Path) -> None:
    """Create the same zero-config profile as the user-level installers."""
    trace_output = Path(
        os.environ.get(
            "ADAMAST_TRACE_OUTPUT",
            default_interactive_trace_output(Path.home()),
        )
    ).expanduser().resolve()
    trace_output.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)

    if host == "claude":
        from adamast.hosts.claude_code.config import ClaudeCodeConfig

        config = ClaudeCodeConfig(
            trace_output=trace_output,
            adamast_model=INTERACTIVE_ADAMAST_MODEL,
            project_scope="auto",
            task_group="default",
            session_selector="prompt",
            selector_surface="browser",
            learning_backend="claude_subagent",
        )
    elif host == "codex":
        from adamast.hosts.codex.config import CodexConfig

        config = CodexConfig(
            trace_output=trace_output,
            adamast_model=INTERACTIVE_ADAMAST_MODEL,
            project_scope="auto",
            task_group="default",
            session_selector="prompt",
            selector_surface="browser",
            learning_backend="codex_subagent",
        )
    else:  # pragma: no cover - argparse and callers constrain this value
        raise ValueError(f"unsupported native plugin host: {host!r}")
    write_json_atomic(path, config.to_dict())


def _host_config_dir(host: str) -> Path:
    if host == "claude":
        configured = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(configured).expanduser() if configured else Path.home() / ".claude"
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _delegate(host: str, event: str, config_path: Path, payload: str) -> int:
    if host == "claude":
        from adamast.hosts.claude_code.dispatcher import main as dispatcher

        dispatcher_args = ["--config", str(config_path)]
    else:
        from adamast.hosts.codex.dispatcher import main as dispatcher

        dispatcher_args = [
            "--config",
            str(config_path),
            "--event",
            event,
        ]
    return _with_stdin(payload, dispatcher, dispatcher_args)


def _with_stdin(
    payload: str,
    dispatcher: Callable[[list[str]], int],
    argv: list[str],
) -> int:
    original_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(payload)
        return dispatcher(argv)
    finally:
        sys.stdin = original_stdin


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
