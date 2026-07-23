"""Codex facade for the shared native taxonomy receipt protocol."""

from __future__ import annotations

from typing import Any

from adamast.hosts.interactive.subagent_protocol import (
    MAX_RECEIPT_CHARS,
    RECEIPT_CLOSE,
    RECEIPT_OPEN,
    capture_learning_receipt,
    complete_learning_job,
    complete_support_review,
    fail_learning_job,
    main,
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
    return _claim_learning_job(
        workspace,
        conversation_id=conversation_id,
        lease_seconds=lease_seconds,
        host_label="Codex",
        subagent_capability="the conversation's subagent/spawn capability",
        forbidden_cli="codex exec",
        expected_host="codex",
        expected_worker_drivers=("codex_native_subagent",),
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
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
