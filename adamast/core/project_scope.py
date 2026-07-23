"""Stable project/group identity for interactive harness programs."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path


_SAFE_SCOPE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_scope_id(value: str, *, label: str) -> str:
    normalized = str(value).strip()
    if not _SAFE_SCOPE_ID.fullmatch(normalized):
        raise ValueError(
            f"{label} must match {_SAFE_SCOPE_ID.pattern}; got {value!r}"
        )
    return normalized


def host_task_group(task_group: str, *, host: str) -> str:
    """Namespace an automatic project group by interactive host."""
    group = validate_scope_id(task_group, label="task_group")
    normalized = str(host or "").strip().casefold().replace("_", "-")
    if normalized not in {"codex", "claude-code"}:
        raise ValueError(f"unsupported interactive host {host!r}")
    prefix = f"{normalized}-"
    candidate = prefix + group
    if len(candidate) > 64:
        digest = hashlib.sha256(group.encode("utf-8", "replace")).hexdigest()[:8]
        candidate = prefix + group[: 64 - len(prefix) - 9] + "-" + digest
    return validate_scope_id(candidate, label="task_group")


def canonical_project_root(cwd: Path | str | None) -> Path:
    """Resolve a Git root when available, otherwise the supplied workspace."""
    path = Path(cwd or Path.cwd()).expanduser().resolve()
    top = _git_top_level(path)
    return Path(top).expanduser().resolve() if top else path


def project_key(
    cwd: Path | str | None,
    *,
    project_id: str | None = None,
) -> str:
    """Return a stable filesystem-safe key without using display metadata."""
    if project_id:
        return validate_scope_id(project_id, label="project_id")
    root = canonical_project_root(cwd)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-._")
    slug = slug[:40] or "project"
    canonical = os.path.normcase(str(root))
    digest = hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{slug}-{digest}"


def project_program_path(
    base: Path | str,
    *,
    cwd: Path | str | None,
    task_group: str = "default",
    project_id: str | None = None,
) -> Path:
    """Resolve the program directory shared by one project task group."""
    group = validate_scope_id(task_group, label="task_group")
    key = project_key(cwd, project_id=project_id)
    return (
        Path(base).expanduser().resolve()
        / "projects"
        / key
        / "groups"
        / group
        / "program"
    )


def _git_top_level(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""
