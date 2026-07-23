"""Durable runtime-evidence export for external dashboards or archives."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .evidence import EVIDENCE_FILE
from .program import MANIFEST_NAME, ProgramWorkspace

DECISION_LOGS = ("decisions.log", "codex-decisions.log")


def export_program_evidence(
    workspace: ProgramWorkspace,
    destination: Path | str,
) -> Path:
    """Write one durable JSON evidence snapshot and return its final path.

    If ``destination`` ends in ``.json`` it is treated as the exact output file.
    Otherwise it is treated as a directory and AdaMAST writes
    ``<program_id>.json`` inside it. The export is a snapshot: it does not move
    or delete runtime files.
    """
    target = _target_path(workspace, Path(destination).expanduser())
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "exported_at_unix": time.time(),
        "program_id": workspace.program_id,
        "trace_output": str(workspace.root),
        "manifest": _read_json(workspace.root / MANIFEST_NAME),
        "runtime_evidence": _read_json(workspace.root / EVIDENCE_FILE),
        "decision_logs": _read_decision_logs(workspace.root),
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return target


def _target_path(workspace: ProgramWorkspace, destination: Path) -> Path:
    if destination.suffix.lower() == ".json":
        return destination.resolve()
    return (destination / f"{workspace.program_id}.json").resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_decision_logs(root: Path) -> dict[str, list[str]]:
    logs: dict[str, list[str]] = {}
    for name in DECISION_LOGS:
        path = root / name
        try:
            logs[name] = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            logs[name] = []
    return logs
