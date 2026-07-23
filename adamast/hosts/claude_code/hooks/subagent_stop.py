from __future__ import annotations

from ..config import ClaudeCodeConfig
from ..runtime import subagent_stop


def handle(event: dict, config: ClaudeCodeConfig) -> tuple[int, dict | str]:
    return subagent_stop(event, config)
