"""Stable project/group identity for interactive harness programs."""

from __future__ import annotations

import hashlib
import json
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
    """Resolve a Git root when available, otherwise the supplied workspace.

    The first successful resolution for a working directory is pinned in a
    small on-disk cache. A later transient git failure, or a ``git init`` in a
    parent directory, therefore cannot silently repartition an existing
    project's history into a different key. The pin is also a performance win:
    it avoids shelling out to git on every interactive event.
    """
    path = Path(cwd or Path.cwd()).expanduser().resolve()
    key = os.path.normcase(str(path))
    pinned = _load_roots_cache().get(key)
    if pinned:
        pinned_path = Path(pinned)
        if pinned_path.exists():
            return pinned_path
        # The pinned target vanished (project moved/deleted); re-resolve below.
    root, cacheable = _resolve_project_root(path)
    if cacheable:
        _store_root_mapping(key, str(root))
    return root


def _resolve_project_root(path: Path) -> tuple[Path, bool]:
    """Return ``(root, cacheable)`` for a resolved working directory.

    ``cacheable`` is False only for a *transient* git failure, so a fallback to
    the raw workspace is never pinned — a later successful resolve can still
    establish the true git root.
    """
    top, transient = _git_top_level(path)
    if top:
        return Path(top).expanduser().resolve(), True
    return path, not transient


def _roots_cache_path() -> Path:
    home = Path(os.environ.get("ADAMAST_HOME", Path.home() / ".adamast"))
    return home.expanduser() / "project_roots.json"


def _load_roots_cache() -> dict[str, str]:
    try:
        raw = _roots_cache_path().read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def _store_root_mapping(key: str, root: str) -> None:
    """Best-effort persist of ``key -> root``; identity still resolves without it."""
    cache = _load_roots_cache()
    if cache.get(key) == root:
        return
    cache[key] = root
    path = _roots_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        return


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


def _git_top_level(path: Path) -> tuple[str, bool]:
    """Return ``(git toplevel or "", transient_error)``.

    ``transient_error`` is True when git could not be run at all (missing
    binary, timeout, OS error) — a state that may resolve later. A clean
    non-zero exit (``not a git repository``) is authoritative, not transient.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return "", True
    if completed.returncode == 0:
        return completed.stdout.strip(), False
    return "", False
