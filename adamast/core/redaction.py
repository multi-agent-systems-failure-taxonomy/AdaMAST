"""Dependency-free helpers for redacting traces before persistence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Pattern

from .traces import GenerationTrace

REDACTION = "[REDACTED]"

DEFAULT_SECRET_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|secret)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"(?i)\b(cookie|set-cookie)\s*:\s*[^\n\r]+"),
)


def redact_text(
    text: str,
    *,
    replacement: str = REDACTION,
    extra_patterns: Sequence[Pattern[str] | str] = (),
) -> str:
    """Return text with common credential-looking substrings replaced."""
    redacted = text
    for pattern in (*DEFAULT_SECRET_PATTERNS, *_compile_extra(extra_patterns)):
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_trace(
    trace: GenerationTrace,
    *,
    replacement: str = REDACTION,
    extra_patterns: Sequence[Pattern[str] | str] = (),
) -> GenerationTrace:
    """Return a copy of a canonical trace with task/body/metadata redacted."""
    compiled = tuple(_compile_extra(extra_patterns))
    return replace(
        trace,
        task=redact_text(
            trace.task,
            replacement=replacement,
            extra_patterns=compiled,
        ),
        raw_trajectory=redact_text(
            trace.raw_trajectory,
            replacement=replacement,
            extra_patterns=compiled,
        ),
        metadata=_redact_value(
            trace.metadata,
            replacement=replacement,
            extra_patterns=compiled,
        ),
    )


def _redact_value(
    value: Any,
    *,
    replacement: str,
    extra_patterns: Sequence[Pattern[str]],
) -> Any:
    if isinstance(value, str):
        return redact_text(
            value,
            replacement=replacement,
            extra_patterns=extra_patterns,
        )
    if isinstance(value, Mapping):
        return {
            key: _redact_value(
                item,
                replacement=replacement,
                extra_patterns=extra_patterns,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _redact_value(
                item,
                replacement=replacement,
                extra_patterns=extra_patterns,
            )
            for item in value
        ]
    return value


def _compile_extra(
    patterns: Sequence[Pattern[str] | str],
) -> tuple[Pattern[str], ...]:
    compiled: list[Pattern[str]] = []
    for pattern in patterns:
        compiled.append(
            re.compile(pattern) if isinstance(pattern, str) else pattern
        )
    return tuple(compiled)
