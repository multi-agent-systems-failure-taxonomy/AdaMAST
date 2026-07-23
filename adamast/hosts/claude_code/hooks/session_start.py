from __future__ import annotations

from ..config import ClaudeCodeConfig
from ..runtime import session_start


def handle(event: dict, config: ClaudeCodeConfig) -> tuple[int, dict]:
    return 0, session_start(event, config)
