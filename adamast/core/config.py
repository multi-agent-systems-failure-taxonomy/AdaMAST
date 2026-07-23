"""Shared dependency-free ``adamast.json`` configuration support."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_FILE = "adamast.json"

PATH_FIELDS = {
    "trace_output",
    "store_dir",
    "trace_root",
    "repo_path",
    "project_dir",
    "evidence_export",
}
INT_FIELDS = {
    "max_retries",
    "format_retries",
    "repair_rounds",
    "generation_threshold",
    "k_init",
    "k",
    "failure_throttle_calls",
    "failure_recency_seconds",
    "recent_activity_messages",
    "recent_activity_chars",
}
BOOL_FIELDS = {
    "generation_stops",
    "skip_judge",
    "refinement_stops",
    "advanced_refinement",
    "dashboard",
    "freeze",
    "redact_traces",
}
STRING_FIELDS = {
    "adamast_model",
    "inherit",
    "repo",
    "openai_base_url",
    "openai_api_key_env",
    "model",
    "gate_exhaustion_policy",
}
RAW_FIELDS = {
    "built_in_hooks",  # legacy Claude Code scope
    "custom_hooks",  # legacy Claude Code scope
    "codex_hooks",  # legacy Codex scope
    "claude_code",
    "codex",
}
ALL_FIELDS = PATH_FIELDS | INT_FIELDS | BOOL_FIELDS | STRING_FIELDS | RAW_FIELDS


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        help=f"path to adamast config JSON (default: ./{DEFAULT_CONFIG_FILE} if present)",
    )


def load_adamast_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load and normalize an ``adamast.json`` object.

    Unknown fields are rejected so typos do not silently change a run. Missing
    files are ignored only when ``path`` is omitted and the default
    ``./adamast.json`` does not exist.
    """
    config_path = Path(path) if path is not None else Path(DEFAULT_CONFIG_FILE)
    if path is None and not config_path.exists():
        return {}
    if not config_path.is_file():
        raise FileNotFoundError(f"AdaMAST config not found: {config_path}")
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid AdaMAST config JSON: {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("AdaMAST config must be a JSON object")
    version = data.get("version", 1)
    if version != 1:
        raise ValueError(f"unsupported AdaMAST config version: {version!r}")
    unknown = set(data) - ALL_FIELDS - {"version"}
    if unknown:
        raise ValueError(f"unknown AdaMAST config field(s): {sorted(unknown)}")
    return {
        key: _normalize_field(key, value, config_path.parent)
        for key, value in data.items()
        if key != "version" and value is not None
    }


def config_value(
    args: argparse.Namespace,
    config: dict[str, Any],
    name: str,
    default: Any = None,
) -> Any:
    """Return explicit CLI value, then config value, then default."""
    value = getattr(args, name, None)
    return value if value is not None else config.get(name, default)


def require_config_value(
    args: argparse.Namespace,
    config: dict[str, Any],
    name: str,
    label: str | None = None,
) -> Any:
    value = config_value(args, config, name)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{label or '--' + name.replace('_', '-')} is required")
    return value


def bool_config_value(
    args: argparse.Namespace,
    config: dict[str, Any],
    name: str,
    default: bool = False,
) -> bool:
    return bool(config_value(args, config, name, default))


def _normalize_field(key: str, value: Any, base: Path) -> Any:
    if key in PATH_FIELDS:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty path string")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        return path.resolve()
    if key in INT_FIELDS:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        if value <= 0:
            raise ValueError(f"{key} must be positive")
        return value
    if key in BOOL_FIELDS:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    if key in STRING_FIELDS:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        stripped = value.strip()
        if key == "inherit" and stripped.lower() == "none":
            return None
        return stripped if stripped else None
    if key in RAW_FIELDS:
        return value
    raise ValueError(f"unknown AdaMAST config field: {key}")
