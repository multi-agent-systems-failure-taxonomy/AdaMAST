"""Host ownership and provenance for interactive AdaMAST programs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from adamast import ProgramWorkspace
from adamast.core.program import ProgramConflict
from adamast.core.project_scope import canonical_project_root
from adamast.core import store

from .session_routes import event_session_id

HOST_LABELS = {
    "codex": "Codex",
    "claude_code": "Claude Code",
}


def normalize_host(value: object) -> str:
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(
        " ", "_"
    )
    if normalized in {"codex", "openai_codex"}:
        return "codex"
    if normalized in {"claude", "claude_code"}:
        return "claude_code"
    raise ValueError(f"unsupported interactive host {value!r}")


def conversation_source(
    host: str,
    event: dict[str, Any],
    *,
    title: str | None = None,
    prompt: str | None = None,
) -> dict[str, str]:
    """Build stable, human-facing provenance for one host conversation."""
    normalized = normalize_host(host)
    root = canonical_project_root(event.get("cwd"))
    session_id = event_session_id(event)
    name = _display_title(title) or _display_title(prompt)
    if not name:
        name = (
            f"Conversation {session_id[:8]}"
            if session_id
            else f"{HOST_LABELS[normalized]} conversation"
        )
    return {
        "host": HOST_LABELS[normalized],
        "host_id": normalized,
        "project": root.name or str(root),
        "project_root": str(root),
        "conversation_id": session_id,
        "conversation_name": name,
    }


def stamp_program_source(
    workspace: ProgramWorkspace,
    source: dict[str, Any],
    *,
    store_dir: Path | str | None = None,
) -> None:
    """Claim a program for one host and refresh its latest conversation source."""
    host = normalize_host(source.get("host_id") or source.get("host"))
    with workspace.locked_manifest() as manifest:
        owners = _program_host_signals(workspace, manifest, store_dir=store_dir)
        if owners and owners != {host}:
            owner = (
                HOST_LABELS[next(iter(owners))]
                if len(owners) == 1
                else "multiple hosts"
            )
            raise ProgramConflict(
                f"program belongs to {owner}, not {HOST_LABELS[host]}"
            )
        taxonomy_id = str(manifest.get("taxonomy_id") or "").strip()
        if taxonomy_id and store_dir is not None:
            try:
                record = store.fetch_by_id(taxonomy_id, store_dir)
            except store.TaxonomyNotFound:
                record = None
            if record is not None and not store.compatible_with_host(record, host):
                owner = store.taxonomy_host(record).replace("_", " ")
                raise ProgramConflict(
                    f"active taxonomy {taxonomy_id!r} is {owner}, not compatible "
                    f"with {HOST_LABELS[host]}"
                )
        manifest["host"] = host
        manifest["source"] = dict(source)


def _program_host_signals(
    workspace: ProgramWorkspace,
    manifest: dict[str, Any],
    *,
    store_dir: Path | str | None,
) -> set[str]:
    """Collect durable ownership signals before claiming a legacy program."""
    signals: set[str] = set()
    manifest_source = manifest.get("source")
    for value in (
        manifest.get("host"),
        manifest_source.get("host_id")
        if isinstance(manifest_source, dict)
        else None,
        manifest_source.get("host") if isinstance(manifest_source, dict) else None,
    ):
        if value:
            signals.add(normalize_host(value))

    state_dirs = {
        "codex": workspace.root / ".adamast-codex",
        "claude_code": workspace.root / ".adamast-claude-code",
    }
    signals.update(owner for owner, path in state_dirs.items() if path.exists())

    taxonomy_id = str(manifest.get("taxonomy_id") or "").strip()
    if taxonomy_id and store_dir is not None:
        try:
            record = store.fetch_by_id(taxonomy_id, store_dir)
        except store.TaxonomyNotFound:
            record = None
        if record is not None:
            owner = store.taxonomy_host(record)
            if owner == "mixed":
                signals.update(HOST_LABELS)
            elif owner != "neutral":
                signals.add(owner)
    return signals


def require_compatible_taxonomy(
    taxonomy_id: str | None,
    *,
    host: str,
    store_dir: Path | str,
) -> None:
    """Reject an explicit or restored cross-host taxonomy before activation."""
    selected = str(taxonomy_id or "").strip()
    if not selected or selected == "mast":
        return
    record = store.fetch_by_id(selected, store_dir)
    if not store.compatible_with_host(record, host):
        owner = store.taxonomy_host(record).replace("_", " ")
        raise ProgramConflict(
            f"taxonomy {selected!r} is {owner}, not compatible with "
            f"{HOST_LABELS[normalize_host(host)]}"
        )


def _display_title(value: object, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    title = re.sub(r"^#+\s*", "", " ".join(value.strip().split())).strip()
    if len(title) > limit:
        return title[: limit - 1].rstrip() + "…"
    return title
