"""Remove project-local or user-level AdaMAST Claude Code hooks."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any

from adamast.hosts.shared import write_json_atomic

DISPATCHER_MODULE = "adamast.hosts.claude_code.dispatcher"
LEGACY_COMMANDS = (
    "adamast-failure-modes",
    "atlas_claude_code",
)
TAXONOMY_WORKER_AGENT = "adamast-taxonomy-worker.md"


def remove_adamast_hooks(
    settings: dict,
    *,
    include_legacy: bool = False,
) -> int:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            if _is_managed_hook_entry(entry, include_legacy=include_legacy):
                removed += 1
            else:
                kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    return removed


def _is_managed_hook_entry(entry: Any, *, include_legacy: bool) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if not isinstance(hook, dict) or hook.get("type") != "command":
            continue
        command = hook.get("command")
        if not isinstance(command, str):
            continue
        try:
            tokens = shlex.split(command)
        except ValueError:
            continue
        if any(
            tokens[index : index + 2] == ["-m", DISPATCHER_MODULE]
            for index in range(max(0, len(tokens) - 1))
        ):
            return True
        if include_legacy and any(token in LEGACY_COMMANDS for token in tokens):
            return True
    return False


def uninstall(
    project_dir: Path | str,
    *,
    migrate_legacy_global: bool = False,
    user_level: bool = False,
) -> dict:
    project_dir = Path(project_dir).resolve()
    claude_dir = Path.home() / ".claude" if user_level else project_dir / ".claude"
    settings_path = claude_dir / (
        "settings.json" if user_level else "settings.local.json"
    )
    removed = _clean_settings(settings_path, include_legacy=False)
    config_path = claude_dir / "adamast.json"
    config_removed = False
    if config_path.is_file():
        config_path.unlink()
        config_removed = True
    agent_path = claude_dir / "agents" / TAXONOMY_WORKER_AGENT
    agent_removed = False
    if agent_path.is_file():
        agent_path.unlink()
        agent_removed = True

    legacy = None
    if migrate_legacy_global and not user_level:
        legacy = _clean_settings(
            Path.home() / ".claude" / "settings.json",
            include_legacy=True,
        )
    return {
        "settings": str(settings_path),
        "removed_hooks": removed,
        "config_removed": config_removed,
        "taxonomy_worker_agent_removed": agent_removed,
        "scope": "user" if user_level else "project",
        "legacy_global_removed_hooks": legacy,
    }


def _clean_settings(path: Path, *, include_legacy: bool) -> int:
    if not path.is_file():
        return 0
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid Claude settings JSON: {path}") from exc
    removed = remove_adamast_hooks(settings, include_legacy=include_legacy)
    write_json_atomic(path, settings)
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Uninstall project-local or user-level AdaMAST Claude Code hooks."
    )
    parser.add_argument("--project-dir", default=None)
    parser.add_argument(
        "--user-level",
        action="store_true",
        help="remove AdaMAST from ~/.claude/settings.json",
    )
    parser.add_argument(
        "--migrate-legacy-global",
        action="store_true",
        help="also remove legacy AdaMAST hooks from ~/.claude/settings.json",
    )
    args = parser.parse_args(argv)
    if args.user_level and args.project_dir is not None:
        parser.error("--user-level cannot be combined with --project-dir")
    result = uninstall(
        args.project_dir or ".",
        migrate_legacy_global=args.migrate_legacy_global,
        user_level=args.user_level,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
