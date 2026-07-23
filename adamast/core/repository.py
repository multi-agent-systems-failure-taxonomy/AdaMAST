"""Display-only repository metadata discovery."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


def discover_repo(
    repo: str | None = None,
    repo_path: Path | str | None = None,
) -> str:
    """Return an explicit label or derive one from git/path context.

    This value is metadata only. It never selects, groups, or routes a taxonomy.
    """
    if repo is not None:
        label = str(repo).strip()
        if not label:
            raise ValueError("repo must be a non-empty string when provided")
        return label

    path = Path(repo_path or Path.cwd()).expanduser().resolve()
    return _discover_from_path(str(path))


@lru_cache(maxsize=128)
def _discover_from_path(path_text: str) -> str:
    path = Path(path_text)
    remote = _git(path, "config", "--get", "remote.origin.url")
    if remote:
        normalized = _remote_label(remote)
        if normalized:
            return normalized
    top = _git(path, "rev-parse", "--show-toplevel")
    if top:
        return Path(top).name
    return path.name


def _git(path: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _remote_label(remote: str) -> str:
    value = remote.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if "://" in value:
        value = value.split("://", 1)[1]
        value = value.split("/", 1)[1] if "/" in value else value
    elif ":" in value:
        value = value.split(":", 1)[1]
    return value.strip("/")
