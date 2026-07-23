"""User-facing CLIs to add, remove, or list ``CustomHookSpec`` entries.

These wrap :class:`ClaudeCodeConfig` so a user can register a new hook
without hand-editing ``adamast.json`` or remembering the exact
``settings.local.json`` shape Claude Code expects.

Run after ``adamast-claude-install`` has placed a config at
``.claude/adamast.json``. Each command rewrites both files atomically.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .config import (
    CLAUDE_CODE_EVENTS,
    CUSTOM_HOOK_MODES,
    CUSTOM_HOOK_CHECKPOINT_KEYS,
    ClaudeCodeConfig,
    CustomHookSpec,
)
from .install import install


def _resolve_config_path(project_dir: Path | str) -> Path:
    project = Path(project_dir).resolve()
    path = project / ".claude" / "adamast.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"no adamast config at {path}; run adamast-claude-install "
            "in this project first"
        )
    return path


def _refresh(project_dir: Path, config: ClaudeCodeConfig) -> dict:
    return install(project_dir, config, verify=False)


def add_hook(
    project_dir: Path | str,
    spec: CustomHookSpec,
    *,
    overwrite: bool = False,
) -> dict:
    project_dir = Path(project_dir).resolve()
    config_path = _resolve_config_path(project_dir)
    config = ClaudeCodeConfig.load(config_path)
    existing = config.find_custom_hook(spec.name)
    if existing is not None and not overwrite:
        raise ValueError(
            f"a custom hook named {spec.name!r} already exists; pass "
            "--overwrite to replace it"
        )
    others = tuple(h for h in config.custom_hooks if h.name != spec.name)
    new_config = replace(config, custom_hooks=others + (spec,))
    result = _refresh(project_dir, new_config)
    result["custom_hook"] = spec.to_dict()
    return result


def remove_hook(project_dir: Path | str, name: str) -> dict:
    project_dir = Path(project_dir).resolve()
    config_path = _resolve_config_path(project_dir)
    config = ClaudeCodeConfig.load(config_path)
    if config.find_custom_hook(name) is None:
        raise KeyError(f"no custom hook named {name!r}")
    new_hooks = tuple(h for h in config.custom_hooks if h.name != name)
    new_config = replace(config, custom_hooks=new_hooks)
    result = _refresh(project_dir, new_config)
    result["removed"] = name
    return result


def list_hooks(project_dir: Path | str) -> list[dict]:
    config_path = _resolve_config_path(project_dir)
    config = ClaudeCodeConfig.load(config_path)
    return [spec.to_dict() for spec in config.custom_hooks]


def _build_parser(verb: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"adamast-claude-{verb}-hook",
        description=(
            "Manage user-declared Claude Code hooks bound to the AdaMAST "
            "reflection<->refinement loop."
        ),
    )
    parser.add_argument("--project-dir", default=".")
    return parser


def add_main(argv=None) -> int:
    parser = _build_parser("add")
    parser.add_argument(
        "--name",
        required=True,
        help="stable identifier (alphanumeric + . _ -); also the gate label",
    )
    parser.add_argument(
        "--event",
        required=True,
        choices=CLAUDE_CODE_EVENTS,
        help="Claude Code hook event to bind",
    )
    parser.add_argument(
        "--mode",
        default="blocking",
        choices=CUSTOM_HOOK_MODES,
        help="blocking: require a parsed reflection before allowing the "
             "event to pass; advisory: emit a nudge as additionalContext "
             "without blocking",
    )
    parser.add_argument(
        "--matcher",
        default=None,
        help="tool-name pattern for PreToolUse/PostToolUse (e.g. \"Bash\")",
    )
    parser.add_argument(
        "--command-pattern",
        default=None,
        help=(
            "optional regex against tool_input/command; useful for narrowing "
            "a Bash hook to one recurring command"
        ),
    )
    parser.add_argument(
        "--checkpoint-key",
        default="tool_use_id",
        choices=CUSTOM_HOOK_CHECKPOINT_KEYS,
        help=(
            "how recurring custom hooks identify an in-flight checkpoint: "
            "tool_use_id, command, or fixed"
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing custom hook with the same --name",
    )
    args = parser.parse_args(argv)
    spec = CustomHookSpec(
        name=args.name,
        event=args.event,
        mode=args.mode,
        matcher=args.matcher,
        command_pattern=args.command_pattern,
        checkpoint_key=args.checkpoint_key,
    )
    try:
        result = add_hook(args.project_dir, spec, overwrite=args.overwrite)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def remove_main(argv=None) -> int:
    parser = _build_parser("remove")
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    try:
        result = remove_hook(args.project_dir, args.name)
    except (FileNotFoundError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def list_main(argv=None) -> int:
    parser = _build_parser("list")
    args = parser.parse_args(argv)
    try:
        data = list_hooks(args.project_dir)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(data, indent=2))
    return 0
