"""Shared adapter primitives used by hook integrations."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from adamast.core.fsio import write_text_atomic_retry


def recorder_command(script_name: str) -> str:
    """Resolve a console script to an absolute path for use inside a prompt.

    Console scripts land next to ``sys.executable`` — ``~/.local/bin`` for
    ``uv tool``/``pipx``, or a venv's ``bin`` — and that directory is routinely
    absent from the PATH the host agent's shell inherits. Handing the agent a
    bare name therefore makes every checkpoint exit 127, and the agent reports
    an operational error instead of recording the gate.

    The hook commands the installers register already embed the absolute
    interpreter path for this same reason; prompts must not be the one place
    that relies on PATH. Fall back to the bare name only when nothing
    resolves, which keeps the prompt readable on an editable checkout.
    """
    candidate = Path(sys.executable).parent / script_name
    for path in (candidate, candidate.with_suffix(".exe")):
        if path.is_file():
            return str(path)
    return shutil.which(script_name) or script_name


def force_utf8_stdio() -> None:
    """Pin hook stdio to UTF-8.

    Hook hosts write the event and read the response as UTF-8, but Python on
    Windows defaults pipes to the ANSI code page — taxonomy text with
    em-dashes reaches the conversation as mojibake, and non-ASCII prompts can
    fail to decode at all.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON via a same-directory temp file and atomic replace.

    Settings files (including the user's global Claude settings) must never
    be left half-written by an interrupted process.
    """
    write_text_atomic_retry(path, json.dumps(data, indent=2) + "\n")


def build_session_state(
    *,
    session_id: str,
    session,
    cwd: str,
    max_retries: int,
    main_cursor: int,
    failure: dict[str, Any],
    episode_sequence: int = 1,
    episode_cursor: int | None = None,
) -> dict[str, Any]:
    """Build the common persisted state envelope for hook adapters."""
    return {
        "version": 1,
        "session_id": session_id,
        "runtime_session_id": session.session_id,
        "program_id": session.program_id,
        "cwd": cwd,
        "taxonomy_id": session.delivery.taxonomy_id,
        "taxonomy": session.delivery.taxonomy,
        "dashboard_url": session.delivery.dashboard_url,
        "max_retries": max_retries,
        "lifecycle": {
            "store_dir": str(session.store_dir),
            "trace_root": str(session.trace_root),
            "generation_threshold": session.generation_threshold,
            "generation_stops": session.generation_stops,
            "skip_judge": session.skip_judge,
            "k_init": session.k_init,
            "k": session.k,
            "refinement_stops": session.refinement_stops,
            "advanced_refinement": session.advanced_refinement,
            "freeze": session.freeze,
            "evidence_export": (
                str(session.evidence_export) if session.evidence_export else None
            ),
            "runtime_protocol": session.delivery.runtime_protocol,
        },
        "main_cursor": main_cursor,
        "episode_sequence": episode_sequence,
        "episode_cursor": (
            main_cursor if episode_cursor is None else episode_cursor
        ),
        "pending": {},
        "failure": failure,
        "finished": False,
        "trace_captured": False,
    }
