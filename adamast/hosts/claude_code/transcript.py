"""Claude Code JSONL transcript helpers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator


_TITLE_EVENT_KEYS = (
    "custom_title",
    "conversation_title",
    "task_title",
    "thread_title",
    "title",
)
_TITLE_TAIL_BYTES = 256_000


def transcript_size(path: Path | str | None) -> int:
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def claude_thread_title(
    session_id: str,
    *,
    transcript_path: Path | str | None = None,
    claude_home: Path | str | None = None,
) -> str:
    """Return the title displayed for a Claude Code conversation."""
    session_id = str(session_id or "").strip()
    if not session_id:
        return ""
    source = Path(transcript_path).expanduser() if transcript_path else None
    if source is None or not source.is_file():
        root = Path(
            claude_home
            or os.environ.get("CLAUDE_CONFIG_DIR")
            or (Path.home() / ".claude")
        ).expanduser()
        source = next(
            (path for path in (root / "projects").glob(f"*/{session_id}.jsonl")),
            None,
        )
    if source is None:
        return ""
    title = _transcript_title(source)
    if title:
        return title
    return _display_title(first_user_message(source))


def resolve_conversation_title(
    session_id: str,
    *,
    event: dict[str, Any] | None = None,
    existing: Any = None,
    prompt: Any = None,
    claude_home: Path | str | None = None,
) -> str:
    """Resolve Claude's stable session ID to its user-facing task title."""
    session_id = str(session_id or "").strip()
    for key in _TITLE_EVENT_KEYS:
        title = _display_title((event or {}).get(key))
        if title:
            return title
    indexed = claude_thread_title(
        session_id,
        transcript_path=(event or {}).get("transcript_path"),
        claude_home=claude_home,
    )
    if indexed:
        return indexed
    current = _display_title(existing)
    placeholder = f"Conversation {session_id[:8]}"
    if current and current != placeholder:
        return current
    prompted = _display_title(prompt)
    if prompted:
        return prompted
    return current or placeholder


def read_transcript(path: Path | str | None, *, after: int = 0) -> str:
    if not path:
        return ""
    source = Path(path)
    try:
        with source.open("rb") as handle:
            handle.seek(min(max(0, after), source.stat().st_size))
            raw = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    chunks: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            chunks.append(line)
            continue
        chunks.extend(_text_chunks(item))
    return "\n".join(chunk for chunk in chunks if chunk.strip())


def read_raw_transcript(
    path: Path | str | None,
    *,
    after: int = 0,
) -> str:
    """Return raw JSONL after a byte cursor, retaining tool interactions."""
    if not path:
        return ""
    try:
        source = Path(path)
        with source.open("rb") as handle:
            handle.seek(min(max(0, after), source.stat().st_size))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def first_user_message(
    path: Path | str | None,
    *,
    after: int = 0,
) -> str:
    """Return the first human-authored message after a byte cursor."""
    messages = user_messages(path, after=after)
    return messages[0] if messages else ""


def user_messages(
    path: Path | str | None,
    *,
    after: int = 0,
) -> list[str]:
    """Return human-authored messages in transcript order."""
    if not path:
        return []
    try:
        source = Path(path)
        with source.open("rb") as handle:
            handle.seek(min(max(0, after), source.stat().st_size))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    messages: list[str] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = item.get("message") if isinstance(item, dict) else None
        role = (
            message.get("role")
            if isinstance(message, dict)
            else item.get("type") if isinstance(item, dict) else None
        )
        if role != "user":
            continue
        chunks = list(_text_chunks(message if message is not None else item))
        text = "\n".join(chunk for chunk in chunks if chunk.strip()).strip()
        if text:
            messages.append(text)
    return messages


def _text_chunks(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _text_chunks(item)
        return
    if not isinstance(value, dict):
        return

    content = value.get("content")
    if isinstance(content, (str, list, dict)):
        yield from _text_chunks(content)
    for key in (
        "message",
        "result",
        "text",
        "thinking",
        "last_assistant_message",
    ):
        item = value.get(key)
        if isinstance(item, (str, list, dict)):
            yield from _text_chunks(item)


def _transcript_title(path: Path) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            offset = max(0, size - _TITLE_TAIL_BYTES)
            handle.seek(offset)
            if offset:
                handle.readline()
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        for key in ("customTitle", "custom_title", "summary"):
            title = _display_title(item.get(key))
            if title:
                return title
    return ""


def _display_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    title = " ".join(value.strip().split())
    title = re.sub(r"^#+\s*", "", title).strip()
    if len(title) > 160:
        title = title[:159].rstrip() + "…"
    return title
