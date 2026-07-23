from __future__ import annotations

from ..config import ClaudeCodeConfig
from ..runtime import post_tool


def handle(event: dict, config: ClaudeCodeConfig) -> tuple[int, dict | None]:
    return 0, post_tool(event, config, execution_failed=False)
