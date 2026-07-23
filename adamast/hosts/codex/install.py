"""Install AdaMAST project-local or user-level Codex hooks."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from adamast.core.config import (
    add_config_argument,
    bool_config_value,
    config_value,
    load_adamast_config,
    require_config_value,
)
from adamast.core.traces import DEFAULT_TRACE_ROOT
from adamast.core import store

from adamast.hosts.interactive.defaults import (
    INTERACTIVE_ADAMAST_MODEL,
    default_interactive_trace_output,
)

from .config import CodexConfig, parse_codex_hooks

SKILL_NAME = "adamast-failure-modes"
SKILL_MARKER_FILE = ".adamast-codex-skill.json"
DISPATCHER_MODULE = "adamast.hosts.codex.dispatcher"
HOOK_STATUS_MESSAGES = {
    "SessionStart": "Restoring AdaMAST taxonomy",
    "UserPromptSubmit": "Checking AdaMAST state",
    "Stop": "Saving AdaMAST trace",
    "SubagentStop": "Reconciling AdaMAST learning",
    "PostToolUse": "Polling AdaMAST",
}


@dataclass(frozen=True)
class CodexSkillInstallResult:
    skill_dir: Path
    skill_md: Path
    agents_openai_yaml: Path
    marker: Path
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_dir": str(self.skill_dir),
            "skill_md": str(self.skill_md),
            "agents_openai_yaml": str(self.agents_openai_yaml),
            "marker": str(self.marker),
            "dry_run": self.dry_run,
        }


def default_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


def install(
    project_dir: Path | str,
    config: CodexConfig,
    *,
    python: Path | str = sys.executable,
    user_level: bool = False,
) -> dict:
    """Install Codex hooks plus the AdaMAST hook config."""
    project_dir = Path(project_dir).resolve()
    codex_dir = Path.home() / ".codex" if user_level else project_dir / ".codex"
    hooks_path = codex_dir / "hooks.json"
    if hooks_path.is_file():
        try:
            hooks_doc = json.loads(hooks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid Codex hooks JSON: {hooks_path}") from exc
        if not isinstance(hooks_doc, dict):
            raise RuntimeError(f"Codex hooks file must be a JSON object: {hooks_path}")
    else:
        hooks_doc = {}

    codex_dir.mkdir(parents=True, exist_ok=True)
    config_path = codex_dir / "adamast.json"
    _write_json_atomic(config_path, config.to_dict())
    remove_adamast_hooks(hooks_doc)
    hooks = hooks_doc.setdefault("hooks", {})
    command = _module_command(Path(python), config_path)
    installed_events: list[str] = []
    for spec in config.hooks:
        if not spec.enabled:
            continue
        entries = hooks.setdefault(spec.event, [])
        matchers = spec.matchers or (None,)
        for matcher in matchers:
            _append_registration(
                entries,
                event=spec.event,
                command=command,
                matcher=matcher,
                timeout_seconds=(
                    config.worker_timeout_seconds + 15
                    if spec.event == "UserPromptSubmit"
                    and config.session_selector == "prompt"
                    and config.selector_surface == "browser"
                    else 30
                ),
            )
        installed_events.append(spec.event)
    _write_json_atomic(hooks_path, hooks_doc)
    return {
        "config": str(config_path),
        "hooks": str(hooks_path),
        "events": installed_events,
        "scope": "user" if user_level else "project",
        "trust_note": "Open /hooks in Codex and trust the new AdaMAST hooks before use.",
    }


def install_user(
    config: CodexConfig,
    *,
    python: Path | str = sys.executable,
) -> dict:
    """Install AdaMAST once for all Codex conversations for this user."""
    return install(Path.home(), config, python=python, user_level=True)


def install_skill(
    *,
    skills_dir: Path | None = None,
    name: str = SKILL_NAME,
    force: bool = False,
    dry_run: bool = False,
) -> CodexSkillInstallResult:
    """Install the optional AdaMAST Codex skill guidance package."""
    if not name or "/" in name or "\\" in name:
        raise ValueError("skill name must be a single directory name")
    target_root = (skills_dir or default_skills_dir()).expanduser()
    skill_dir = target_root / name
    skill_md = skill_dir / "SKILL.md"
    agents_dir = skill_dir / "agents"
    openai_yaml = agents_dir / "openai.yaml"
    marker = skill_dir / SKILL_MARKER_FILE
    if skill_md.exists() and not force and not _is_managed_skill(marker, name):
        raise FileExistsError(
            f"{skill_md} already exists and is not marked as AdaMAST-managed; "
            "pass --force to replace the known skill files"
        )
    result = CodexSkillInstallResult(
        skill_dir=skill_dir,
        skill_md=skill_md,
        agents_openai_yaml=openai_yaml,
        marker=marker,
        dry_run=dry_run,
    )
    if dry_run:
        return result
    skill_dir.mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)
    skill_md.write_text(_asset_text("SKILL.md"), encoding="utf-8")
    openai_yaml.write_text(_asset_text("openai.yaml"), encoding="utf-8")
    marker.write_text(
        json.dumps(
            {
                "managed_by": "adamast",
                "integration": "codex",
                "skill_name": name,
                "files": ["SKILL.md", "agents/openai.yaml"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return result


def _is_managed_skill(marker: Path, name: str) -> bool:
    if not marker.is_file():
        return False
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(record, dict)
        and record.get("managed_by") == "adamast"
        and record.get("integration") == "codex"
        and record.get("skill_name") == name
    )


def remove_adamast_hooks(hooks_doc: dict) -> int:
    hooks = hooks_doc.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            if _is_managed_hook_entry(entry, module=DISPATCHER_MODULE):
                removed += 1
            else:
                kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        hooks_doc.pop("hooks", None)
    return removed


def _is_managed_hook_entry(entry: Any, *, module: str) -> bool:
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
            tokens[index : index + 2] == ["-m", module]
            for index in range(max(0, len(tokens) - 1))
        ):
            return True
    return False


def _append_registration(
    entries: list,
    *,
    event: str,
    command: str,
    matcher: str | None,
    timeout_seconds: int,
) -> None:
    registration = {
        **({"matcher": matcher} if matcher else {}),
        "hooks": [
            {
                "type": "command",
                "command": command,
                # The browser UserPromptSubmit path pauses synchronously until
                # the user chooses. Other lifecycle hooks stay short.
                "timeout": max(30, int(timeout_seconds)),
                "statusMessage": HOOK_STATUS_MESSAGES.get(event, "Running AdaMAST gate"),
            }
        ],
    }
    if not any(
        entry.get("matcher") == matcher
        and any(hook.get("command") == command for hook in entry.get("hooks", []))
        for entry in entries
    ):
        entries.append(registration)


def _module_command(python: Path, config: Path) -> str:
    parts = [
        _hook_interpreter_path(python),
        "-m",
        "adamast.hosts.codex.dispatcher",
        "--config",
        _hook_shell_path(config),
    ]
    return shlex.join(parts)


def _hook_shell_path(path: Path) -> str:
    resolved = str(path.resolve())
    return resolved.replace("\\", "/") if os.name == "nt" else resolved


def _hook_interpreter_path(path: Path) -> str:
    # The interpreter path must NOT follow symlinks: on POSIX a venv's
    # bin/python is a symlink to the base interpreter, and resolving it
    # registers hook commands against the base install — which lacks (or
    # ships a stale copy of) this package. Normalize without resolving so
    # the hooks keep running inside the environment that installed them.
    absolute = str(Path(os.path.abspath(str(path))))
    return absolute.replace("\\", "/") if os.name == "nt" else absolute


def _asset_text(name: str) -> str:
    return (
        files("adamast.hosts.codex")
        .joinpath("assets").joinpath(name)
        .read_text(encoding="utf-8")
    )


def _write_json_atomic(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install project-local or user-level AdaMAST Codex hooks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "quickstart:\n"
            "  adamast-codex-install --user-level   # all Codex projects\n"
            "  adamast-doctor --codex               # verify the install\n"
            "Most flags below are optional tuning; the defaults are fine."
        ),
    )

    start_group = parser.add_argument_group(
        "getting started", "the flags most installs need"
    )
    learning_group = parser.add_argument_group(
        "learning behavior", "when and how taxonomies are generated and refined"
    )
    advanced_group = parser.add_argument_group(
        "advanced tuning", "retry budgets, scoping, storage, and model plumbing"
    )

    add_config_argument(start_group)
    start_group.add_argument("--project-dir", default=None)
    advanced_group.add_argument(
        "--user-level",
        action="store_true",
        help="install in ~/.codex/hooks.json for all Codex projects",
    )
    start_group.add_argument("--trace-output", "--trace_output", dest="trace_output", type=Path)
    start_group.add_argument("--adamast-model", "--adamast_model", dest="adamast_model")
    advanced_group.add_argument("--store-dir", "--store_dir", dest="store_dir", type=Path)
    advanced_group.add_argument("--trace-root", "--trace_root", dest="trace_root", type=Path)
    start_group.add_argument("--inherit")
    advanced_group.add_argument("--max-retries", "--max_retries", dest="max_retries", type=int)
    learning_group.add_argument("--generation-threshold", "--generation_threshold", dest="generation_threshold", type=int)
    learning_group.add_argument("--generation-stops", "--generation_stops", dest="generation_stops", action=argparse.BooleanOptionalAction)
    learning_group.add_argument("--skip-judge", "--skip_judge", dest="skip_judge", action="store_true", default=None)
    learning_group.add_argument("--k-init", "--k_init", dest="k_init", type=int)
    learning_group.add_argument("--k", type=int)
    learning_group.add_argument("--refinement-stops", "--refinement_stops", dest="refinement_stops", action=argparse.BooleanOptionalAction)
    learning_group.add_argument("--advanced-refinement", "--advanced_refinement", dest="advanced_refinement", action=argparse.BooleanOptionalAction)
    learning_group.add_argument("--freeze", dest="freeze", action=argparse.BooleanOptionalAction)
    advanced_group.add_argument("--evidence-export", "--evidence_export", dest="evidence_export", type=Path)
    advanced_group.add_argument("--no-dashboard", dest="dashboard", action="store_false", default=None)
    advanced_group.add_argument("--openai-base-url", "--openai_base_url", dest="openai_base_url")
    advanced_group.add_argument("--openai-api-key-env", "--openai_api_key_env", dest="openai_api_key_env")
    advanced_group.add_argument(
        "--project-scope",
        choices=("explicit", "auto"),
        default=None,
        help="use trace_output directly or derive one program per project",
    )
    advanced_group.add_argument("--project-id", default=None)
    advanced_group.add_argument("--task-group", default=None)
    advanced_group.add_argument(
        "--session-selector",
        choices=("off", "prompt"),
        default=None,
        help="ask for a taxonomy when a new Codex conversation starts",
    )
    advanced_group.add_argument(
        "--selector-surface",
        choices=("browser", "inline"),
        default=None,
        help="open the browser taxonomy library or use the inline fallback",
    )
    advanced_group.add_argument(
        "--learning-backend",
        choices=("provider", "codex_subagent"),
        default=None,
        help="use provider API calls or the authenticated Codex subagent worker",
    )
    advanced_group.add_argument("--worker-model", default=None)
    advanced_group.add_argument("--codex-cli-path", type=Path, default=None)
    advanced_group.add_argument("--worker-timeout-seconds", type=int, default=None)
    advanced_group.add_argument(
        "--disable-hook",
        action="append",
        default=[],
        choices=(
            "SessionStart",
            "UserPromptSubmit",
            "Stop",
            "SubagentStop",
            "PostToolUse",
        ),
        help="do not install a built-in Codex hook event",
    )
    advanced_group.add_argument(
        "--post-tool-use-matchers",
        default=None,
        help="comma-separated Codex PostToolUse matcher regexes",
    )
    advanced_group.add_argument(
        "--install-skill",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "install the AdaMAST Codex skill guidance package; enabled by "
            "default for --user-level"
        ),
    )
    advanced_group.add_argument("--skills-dir", type=Path, default=None)
    advanced_group.add_argument("--force-skill", action="store_true")
    args = parser.parse_args(argv)
    if args.user_level and args.project_dir is not None:
        parser.error("--user-level cannot be combined with --project-dir")
    try:
        config_doc = (
            load_adamast_config(args.config)
            if args.config is not None or not args.user_level
            else {}
        )
        trace_output_value = config_value(args, config_doc, "trace_output")
        adamast_model_value = config_value(args, config_doc, "adamast_model")
        if args.user_level:
            trace_output_value = trace_output_value or default_interactive_trace_output(
                Path.home()
            )
            adamast_model_value = adamast_model_value or INTERACTIVE_ADAMAST_MODEL
        else:
            trace_output_value = require_config_value(
                args, config_doc, "trace_output", "--trace-output"
            )
            adamast_model_value = require_config_value(
                args, config_doc, "adamast_model", "--adamast-model"
            )
    except Exception as exc:  # noqa: BLE001
        parser.error(str(exc))
    adapter_config = (
        config_doc.get("codex")
        if isinstance(config_doc.get("codex"), dict)
        else {}
    )
    hooks_doc = dict(adapter_config.get("hooks", config_doc.get("codex_hooks")) or {})
    for event in args.disable_hook:
        hooks_doc[event] = False
    if args.post_tool_use_matchers is not None:
        hooks_doc["PostToolUse"] = {
            "enabled": True,
            "matchers": [
                item.strip()
                for item in args.post_tool_use_matchers.split(",")
                if item.strip()
            ],
        }
    cfg = CodexConfig(
        trace_output=Path(trace_output_value).expanduser().resolve(),
        adamast_model=str(adamast_model_value),
        store_dir=Path(config_value(args, config_doc, "store_dir", store.DEFAULT_STORE_DIR)),
        trace_root=Path(config_value(args, config_doc, "trace_root", DEFAULT_TRACE_ROOT)),
        inherit=args.inherit if args.inherit is not None else config_doc.get("inherit"),
        dashboard=bool_config_value(args, config_doc, "dashboard", True),
        openai_base_url=config_value(args, config_doc, "openai_base_url"),
        openai_api_key_env=config_value(args, config_doc, "openai_api_key_env"),
        max_retries=int(config_value(args, config_doc, "max_retries", 3)),
        generation_threshold=int(config_value(args, config_doc, "generation_threshold", 5)),
        generation_stops=bool_config_value(args, config_doc, "generation_stops", False),
        skip_judge=bool_config_value(args, config_doc, "skip_judge", False),
        k_init=int(config_value(args, config_doc, "k_init", 10)),
        k=int(config_value(args, config_doc, "k", 20)),
        refinement_stops=bool_config_value(args, config_doc, "refinement_stops", False),
        advanced_refinement=bool_config_value(args, config_doc, "advanced_refinement", False),
        freeze=bool_config_value(args, config_doc, "freeze", False),
        redact_traces=bool_config_value(args, config_doc, "redact_traces", True),
        evidence_export=(
            Path(config_value(args, config_doc, "evidence_export"))
            if config_value(args, config_doc, "evidence_export")
            else None
        ),
        project_scope=(
            args.project_scope
            or str(
                adapter_config.get(
                    "project_scope",
                    "auto" if args.user_level else "explicit",
                )
            )
        ),
        project_id=(
            args.project_id
            if args.project_id is not None
            else adapter_config.get("project_id")
        ),
        task_group=(
            args.task_group
            or str(adapter_config.get("task_group", "default"))
        ),
        session_selector=(
            args.session_selector
            or str(
                adapter_config.get(
                    "session_selector",
                    "prompt" if args.user_level else "off",
                )
            )
        ),
        selector_surface=(
            args.selector_surface
            or str(adapter_config.get("selector_surface", "browser"))
        ),
        learning_backend=(
            args.learning_backend
            or str(
                adapter_config.get(
                    "learning_backend",
                    "codex_subagent" if args.user_level else "provider",
                )
            )
        ),
        worker_model=(
            args.worker_model
            if args.worker_model is not None
            else adapter_config.get("worker_model")
        ),
        codex_cli_path=(
            args.codex_cli_path
            if args.codex_cli_path is not None
            else (
                Path(str(adapter_config["codex_cli_path"]))
                if adapter_config.get("codex_cli_path")
                else None
            )
        ),
        worker_timeout_seconds=(
            args.worker_timeout_seconds
            if args.worker_timeout_seconds is not None
            else int(adapter_config.get("worker_timeout_seconds", 1800))
        ),
        hooks=parse_codex_hooks(hooks_doc),
    )
    if args.user_level:
        result = install_user(cfg)
    else:
        result = install(config_value(args, config_doc, "project_dir", "."), cfg)
    install_skill_enabled = (
        args.install_skill if args.install_skill is not None else args.user_level
    )
    if install_skill_enabled:
        result["skill"] = install_skill(
            skills_dir=args.skills_dir,
            force=args.force_skill,
        ).to_dict()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
