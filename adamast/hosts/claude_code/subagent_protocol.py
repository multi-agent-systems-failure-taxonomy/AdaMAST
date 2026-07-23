"""Claude Code facade for the shared native taxonomy receipt protocol."""

from __future__ import annotations

import os
from typing import Any

from adamast.hosts.interactive.subagent_protocol import (
    MAX_RECEIPT_CHARS,
    RECEIPT_CLOSE,
    RECEIPT_OPEN,
    capture_learning_receipt,
    complete_learning_job,
    complete_support_review,
    fail_learning_job,
)
from adamast.hosts.interactive.subagent_protocol import (
    claim_learning_job as _claim_learning_job,
)
from adamast import ProgramWorkspace


def claim_learning_job(
    workspace: ProgramWorkspace,
    *,
    conversation_id: str,
    lease_seconds: int = 1800,
) -> dict[str, Any] | None:
    """Claim one job and render instructions for Claude Code's Agent tool."""
    if os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "").strip() == "1":
        # Never turn taxonomy learning into foreground work. Leave the durable
        # job queued until a background-capable Claude session claims it.
        return None
    return _claim_learning_job(
        workspace,
        conversation_id=conversation_id,
        lease_seconds=lease_seconds,
        host_label="Claude Code",
        subagent_capability=(
            "Claude Code's native Agent tool with "
            "subagent_type=`adamast-taxonomy-worker` and "
            "run_in_background=`true`"
        ),
        forbidden_cli="claude -p or a standalone Claude process",
        expected_host="claude_code",
        expected_worker_drivers=("claude_native_subagent",),
    )


__all__ = [
    "MAX_RECEIPT_CHARS",
    "RECEIPT_CLOSE",
    "RECEIPT_OPEN",
    "capture_learning_receipt",
    "claim_learning_job",
    "complete_learning_job",
    "complete_support_review",
    "fail_learning_job",
]
