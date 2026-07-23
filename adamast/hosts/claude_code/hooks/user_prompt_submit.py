from __future__ import annotations

from ..config import ClaudeCodeConfig
from ..runtime import user_prompt_submit


def handle(event: dict, config: ClaudeCodeConfig) -> tuple[int, dict | None]:
    return 0, user_prompt_submit(event, config)
