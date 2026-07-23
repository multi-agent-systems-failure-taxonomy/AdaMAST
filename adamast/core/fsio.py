"""Windows-safe file primitives for state shared across hooks and workers.

CPython opens files without FILE_SHARE_DELETE, so on Windows an
``os.replace`` racing a concurrent reader — or a reader racing an in-flight
replace — raises ``PermissionError`` even though both sides are correct.
Writers hold the file for microseconds, so bounded retry converges; the
original error is re-raised when the deadline passes.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

_RETRY_DEADLINE_SECONDS = 2.0
_RETRY_INITIAL_SLEEP = 0.001
_RETRY_MAX_SLEEP = 0.05


def read_text_retry(path: Path | str, *, encoding: str = "utf-8") -> str:
    """``Path.read_text`` that outlasts a concurrent atomic replace."""
    path = Path(path)
    deadline = time.monotonic() + _RETRY_DEADLINE_SECONDS
    sleep = _RETRY_INITIAL_SLEEP
    while True:
        try:
            return path.read_text(encoding=encoding)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(sleep)
            sleep = min(sleep * 2, _RETRY_MAX_SLEEP)


def replace_retry(source: Path | str, target: Path | str) -> None:
    """``os.replace`` that outlasts concurrently open readers of ``target``."""
    deadline = time.monotonic() + _RETRY_DEADLINE_SECONDS
    sleep = _RETRY_INITIAL_SLEEP
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(sleep)
            sleep = min(sleep * 2, _RETRY_MAX_SLEEP)


def write_text_atomic_retry(
    path: Path | str,
    text: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Write via a process-unique temporary file and a retried replace."""
    path = Path(path)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    temporary.write_text(text, encoding=encoding)
    try:
        replace_retry(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
