from __future__ import annotations

from ..config import ClaudeCodeConfig
from ..runtime import blocking_checkpoint


def handle(event: dict, config: ClaudeCodeConfig) -> tuple[int, str]:
    return blocking_checkpoint(event, config, gate="task_completed")
