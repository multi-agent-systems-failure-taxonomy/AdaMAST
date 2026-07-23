"""Codex transcript normalization for learning traces.

Codex exposes ``transcript_path`` as a convenience rather than a stable API.
Keep the adapter deliberately conservative: retain human/assistant messages and
tool interactions, while dropping developer context, reasoning, hook prompts,
token accounting, and other harness-owned records.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator


MAX_EVENT_CHARS = 16_000
MAX_TRACE_CHARS = 750_000
TRACE_HEAD_CHARS = 150_000
_INTERNAL_USER_PREFIXES = (
    "<hook_prompt",
    "<environment_context",
    "<skills_instructions",
    "<permissions instructions",
    "<app-context",
    "<collaboration_mode",
    "<plugins_instructions",
    "<apps_instructions",
)
_WORKDIR = re.compile(
    r"(?i)[\"']?(?:workdir|cwd)[\"']?\s*[:=]\s*[\"']([^\"']+)[\"']"
)
_INSTALLED_SKILL_PATH = re.compile(
    r"(?i)(?:^|[\\/])\.(?:agents|codex)[\\/](?:skills|plugins)[\\/].*"
    r"[\\/]SKILL\.md(?:\b|$)"
)
_TITLE_EVENT_KEYS = (
    "thread_name",
    "conversation_title",
    "task_title",
    "thread_title",
    "title",
)
_TITLE_INDEX_KEYS = ("thread_name", "title", "name")


def transcript_size(path: Path | str | None) -> int:
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def codex_thread_title(
    session_id: str,
    *,
    codex_home: Path | str | None = None,
) -> str:
    """Return Codex's user-facing task title from its local session index."""
    session_id = str(session_id or "").strip()
    if not session_id:
        return ""
    root = Path(
        codex_home
        or os.environ.get("CODEX_HOME")
        or (Path.home() / ".codex")
    ).expanduser()
    try:
        lines = (root / "session_index.jsonl").read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
    except OSError:
        return ""
    # Prefer the newest matching record if Codex appends a rename.
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        indexed_id = item.get("id") or item.get("session_id") or item.get("thread_id")
        if str(indexed_id or "").strip() != session_id:
            continue
        for key in _TITLE_INDEX_KEYS:
            title = _display_title(item.get(key))
            if title:
                return title
    return ""


def resolve_conversation_title(
    session_id: str,
    *,
    event: dict[str, Any] | None = None,
    existing: Any = None,
    prompt: Any = None,
    codex_home: Path | str | None = None,
) -> str:
    """Resolve a stable ID to the title a person sees in Codex."""
    session_id = str(session_id or "").strip()
    for key in _TITLE_EVENT_KEYS:
        title = _display_title((event or {}).get(key))
        if title:
            return title
    indexed = codex_thread_title(session_id, codex_home=codex_home)
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


def read_raw_transcript(
    path: Path | str | None,
    *,
    after: int = 0,
) -> str:
    """Return normalized JSONL suitable for an AdaMAST learning trace."""
    rendered = [
        json.dumps(item, ensure_ascii=False, default=str)
        for item in _normalized_events(path, after=after)
    ]
    return _bounded_trace(rendered)


def read_transcript(path: Path | str | None, *, after: int = 0) -> str:
    """Return compact readable text from the normalized Codex transcript."""
    chunks: list[str] = []
    for item in _normalized_events(path, after=after):
        kind = str(item.get("type") or "event")
        if kind in {"user", "assistant"}:
            chunks.append(f"{kind}: {item.get('text', '')}")
        elif kind == "tool_call":
            chunks.append(
                f"tool_call {item.get('tool', 'unknown')}: "
                f"{_stringify(item.get('input'))}"
            )
        elif kind == "tool_result":
            chunks.append(f"tool_result: {_stringify(item.get('output'))}")
    return "\n".join(chunk for chunk in chunks if chunk.strip())


def first_user_message(
    path: Path | str | None,
    *,
    after: int = 0,
) -> str:
    for item in _normalized_events(path, after=after):
        if item.get("type") == "user":
            return str(item.get("text") or "").strip()
    return ""


def latest_user_message(
    path: Path | str | None,
    *,
    after: int = 0,
) -> str:
    latest = ""
    for item in _normalized_events(path, after=after):
        if item.get("type") == "user":
            latest = str(item.get("text") or "").strip()
    return latest


def user_messages(
    path: Path | str | None,
    *,
    after: int = 0,
) -> list[str]:
    """Return normalized user messages in transcript order."""
    return [
        str(item.get("text") or "").strip()
        for item in _normalized_events(path, after=after)
        if item.get("type") == "user"
    ]


def has_assistant_activity(
    path: Path | str | None,
    *,
    after: int = 0,
) -> bool:
    return any(
        item.get("type") in {"assistant", "tool_call", "tool_result"}
        for item in _normalized_events(path, after=after)
    )


def trace_has_assistant_activity(raw_trajectory: str) -> bool:
    for line in str(raw_trajectory or "").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("type") in {
            "assistant",
            "tool_call",
            "tool_result",
        }:
            return True
    return False


def external_workdirs(
    raw_trajectory: str,
    *,
    bound_root: Path | str | None,
) -> list[str]:
    """Find explicit tool workdirs outside the project bound to the task."""
    if not bound_root:
        return []
    try:
        bound = Path(bound_root).expanduser().resolve()
    except OSError:
        return []
    found: set[str] = set()
    for line in str(raw_trajectory or "").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("type") != "tool_call":
            continue
        for candidate in _candidate_workdirs(item.get("input")):
            try:
                path = Path(candidate).expanduser().resolve()
            except (OSError, ValueError):
                continue
            if not _is_relative_to(path, bound):
                found.add(str(path))
    return sorted(found)


def _normalized_events(
    path: Path | str | None,
    *,
    after: int,
) -> Iterator[dict[str, Any]]:
    seen_messages: set[tuple[str, str]] = set()
    suppressed_calls: set[str] = set()
    for item in _json_items(path, after=after):
        normalized = _normalize_item(item)
        if normalized is None:
            continue
        kind = str(normalized.get("type") or "")
        call_id = str(normalized.get("call_id") or "")
        if kind == "tool_call" and _installed_skill_read(normalized.get("input")):
            if call_id:
                suppressed_calls.add(call_id)
            continue
        if kind == "tool_result" and call_id in suppressed_calls:
            continue
        if kind in {"user", "assistant"}:
            text = str(normalized.get("text") or "").strip()
            if not text or (kind == "user" and _internal_user_text(text)):
                continue
            key = (kind, text)
            if key in seen_messages:
                continue
            seen_messages.add(key)
            normalized["text"] = _bounded(text)
        elif kind == "tool_call":
            normalized["input"] = _bounded_value(normalized.get("input"))
        elif kind == "tool_result":
            normalized["output"] = _bounded_value(normalized.get("output"))
        yield normalized


def _json_items(
    path: Path | str | None,
    *,
    after: int,
) -> Iterator[dict[str, Any]]:
    if not path:
        return
    source = Path(path)
    try:
        size = source.stat().st_size
        offset = min(max(0, int(after)), size)
        with source.open("rb") as handle:
            if offset and offset < size:
                handle.seek(offset - 1)
                if handle.read(1) not in {b"\n", b"\r"}:
                    handle.readline()
            else:
                handle.seek(offset)
            raw = handle.read().decode("utf-8", "replace")
    except OSError:
        return
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            yield item


def _normalize_item(item: dict[str, Any]) -> dict[str, Any] | None:
    outer_type = item.get("type")
    payload = item.get("payload")

    if outer_type == "event_msg" and isinstance(payload, dict):
        payload_type = payload.get("type")
        if payload_type == "user_message":
            return {"type": "user", "text": _message_text(payload.get("message"))}
        if payload_type == "agent_message":
            return {
                "type": "assistant",
                "phase": payload.get("phase"),
                "text": _message_text(payload.get("message")),
            }
        return None

    if outer_type == "response_item" and isinstance(payload, dict):
        payload_type = str(payload.get("type") or "")
        if payload_type == "message" and payload.get("role") in {"user", "assistant"}:
            return {
                "type": str(payload["role"]),
                "phase": payload.get("phase"),
                "text": _message_text(payload.get("content")),
            }
        if payload_type in {
            "custom_tool_call",
            "function_call",
            "mcp_tool_call",
            "local_shell_call",
        }:
            return {
                "type": "tool_call",
                "call_id": payload.get("call_id") or payload.get("id"),
                "tool": payload.get("name") or payload_type,
                "input": (
                    payload.get("input")
                    if "input" in payload
                    else payload.get("arguments")
                    if "arguments" in payload
                    else payload.get("command")
                ),
            }
        if payload_type in {
            "custom_tool_call_output",
            "function_call_output",
            "mcp_tool_call_output",
            "local_shell_call_output",
        }:
            return {
                "type": "tool_result",
                "call_id": payload.get("call_id") or payload.get("id"),
                "output": payload.get("output") or payload.get("result"),
            }
        return None

    # Simple fixtures and older compatible transcript records.
    if outer_type in {"user", "assistant"}:
        message = item.get("message", item)
        return {
            "type": str(outer_type),
            "text": _message_text(message),
        }
    return None


def _message_text(value: Any) -> str:
    chunks = list(_text_chunks(value))
    return "\n".join(chunk for chunk in chunks if chunk.strip()).strip()


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
    if value.get("type") in {"input_text", "output_text", "text"}:
        text = value.get("text")
        if isinstance(text, str):
            yield text
            return
    for key in ("content", "message", "text"):
        nested = value.get(key)
        if isinstance(nested, (str, list, dict)):
            yield from _text_chunks(nested)


def _internal_user_text(text: str) -> bool:
    normalized = text.lstrip().lower()
    return normalized.startswith(_INTERNAL_USER_PREFIXES)


def _display_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    title = " ".join(value.strip().split())
    marker = "## My request for Codex:"
    if marker in title:
        title = title.rsplit(marker, 1)[-1].strip()
    title = re.sub(r"^#+\s*", "", title).strip()
    if len(title) > 160:
        title = title[:159].rstrip() + "…"
    return title


def _installed_skill_read(value: Any) -> bool:
    """Drop harness guidance reads without hiding repo-owned skill work."""
    rendered = _stringify(value).replace("\\\\", "\\")
    return bool(_INSTALLED_SKILL_PATH.search(rendered))


def _bounded(value: str) -> str:
    if len(value) <= MAX_EVENT_CHARS:
        return value
    omitted = len(value) - MAX_EVENT_CHARS
    return value[:MAX_EVENT_CHARS] + f"\n[AdaMAST omitted {omitted} characters]"


def _bounded_value(value: Any) -> Any:
    if isinstance(value, str):
        return _bounded(value)
    rendered = _stringify(value)
    if len(rendered) <= MAX_EVENT_CHARS:
        return value
    return _bounded(rendered)


def _bounded_trace(lines: list[str]) -> str:
    full = "\n".join(lines)
    if len(full) <= MAX_TRACE_CHARS:
        return full
    head: list[str] = []
    tail: list[str] = []
    head_size = 0
    for line in lines:
        if head_size + len(line) + 1 > TRACE_HEAD_CHARS:
            break
        head.append(line)
        head_size += len(line) + 1
    tail_budget = MAX_TRACE_CHARS - head_size - 120
    tail_size = 0
    for line in reversed(lines[len(head) :]):
        if tail_size + len(line) + 1 > tail_budget:
            break
        tail.append(line)
        tail_size += len(line) + 1
    omitted = max(0, len(lines) - len(head) - len(tail))
    marker = json.dumps(
        {"type": "adamast_trace_truncation", "omitted_events": omitted}
    )
    return "\n".join([*head, marker, *reversed(tail)])


def _candidate_workdirs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in {"workdir", "cwd"} and isinstance(nested, str):
                yield nested
            else:
                yield from _candidate_workdirs(nested)
        return
    if isinstance(value, list):
        for nested in value:
            yield from _candidate_workdirs(nested)
        return
    if not isinstance(value, str):
        return
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = None
    if decoded is not None and decoded != value:
        yield from _candidate_workdirs(decoded)
    for match in _WORKDIR.finditer(value):
        yield match.group(1).replace("\\\\", "\\")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)
