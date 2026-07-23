"""Persistent per-session hook state for the Codex integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adamast.core.fsio import read_text_retry, write_text_atomic_retry

SESSION_DIR = ".adamast-codex"


def state_path(trace_output: Path, session_id: str) -> Path:
    safe = "".join(
        char if char.isalnum() or char in "._-" else "_"
        for char in session_id
    )
    return trace_output / SESSION_DIR / f"{safe}.json"


def load_state(trace_output: Path, session_id: str) -> dict[str, Any]:
    path = state_path(trace_output, session_id)
    try:
        # A transient sharing violation must not read as "new session": that
        # verdict re-runs session setup and re-prompts the taxonomy selector
        # mid-conversation.
        return json.loads(read_text_retry(path))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(trace_output: Path, session_id: str, state: dict[str, Any]) -> None:
    path = state_path(trace_output, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic_retry(
        path,
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
    )
