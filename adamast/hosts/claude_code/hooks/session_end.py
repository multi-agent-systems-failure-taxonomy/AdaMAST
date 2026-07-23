from __future__ import annotations

from ..config import ClaudeCodeConfig
from ..runtime import session_end


def handle(event: dict, config: ClaudeCodeConfig) -> tuple[int, str | None]:
    return session_end(event, config)
