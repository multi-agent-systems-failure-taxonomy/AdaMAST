"""Claude-native learning jobs built on the shared durable coordinator."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable

from adamast import ProgramWorkspace

from adamast.learning.learning_jobs import (
    JOBS_DIR,
    TERMINAL_STATES,
    LearningJobError,
    _finish_unsuccessful,
    _job_lock,
    _read_json,
    drain_learning_notices,
    enqueue_learning_job as _enqueue_learning_job,
    poll_learning_jobs as _poll_learning_jobs,
    reconcile_learning_jobs,
)
from adamast.learning.worker_contract import build_prompt, candidate_schema
from adamast.core.fsio import write_text_atomic_retry

from .subagent_protocol import capture_learning_receipt, claim_learning_job


def resolve_claude_cli(explicit: Path | str | None = None) -> Path:
    """Locate Claude Code without reading or copying its credentials."""
    candidates = [
        str(explicit) if explicit else None,
        os.environ.get("CLAUDE_CLI_PATH"),
        shutil.which("claude.cmd"),
        shutil.which("claude"),
        str(Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd")
        if os.environ.get("APPDATA")
        else None,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        if path.is_file():
            return path
    raise LearningJobError(
        "Claude Code CLI was not found; set claude_code.claude_cli_path"
    )


def enqueue_claude_learning_job(
    workspace: ProgramWorkspace,
    *,
    kind: str,
    store_dir: Path | str,
    trace_root: Path | str,
    task_group: str,
    conversation_id: str,
    worker_model: str | None = None,
    claude_cli_path: Path | str | None = None,
    worker_timeout_seconds: int = 1800,
    launcher: Callable[[Path], None] | None = None,
) -> str:
    if launcher is not None:
        resolved_cli = resolve_claude_cli(claude_cli_path)
        return _enqueue_learning_job(
            workspace,
            kind=kind,
            store_dir=store_dir,
            trace_root=trace_root,
            task_group=task_group,
            conversation_id=conversation_id,
            worker_model=worker_model,
            worker_cli_path=resolved_cli,
            worker_timeout_seconds=worker_timeout_seconds,
            worker_driver="claude_subagent",
            worker_label="Claude Code taxonomy subagent",
            worker_module="adamast.hosts.claude_code.native_worker",
            job_prefix="claude",
            launcher=launcher,
        )
    job_id = _enqueue_learning_job(
        workspace,
        kind=kind,
        store_dir=store_dir,
        trace_root=trace_root,
        task_group=task_group,
        conversation_id=conversation_id,
        worker_model=None,
        worker_timeout_seconds=worker_timeout_seconds,
        worker_driver="claude_native_subagent",
        worker_label="Claude Code in-task taxonomy subagent",
        worker_module="adamast.hosts.claude_code.subagent_protocol",
        job_prefix="claude-native",
        dispatch_mode="host_subagent",
    )
    _prepare_job_files(Path(workspace.root) / JOBS_DIR / job_id)
    return job_id


def poll_learning_jobs(
    workspace: ProgramWorkspace,
    *,
    store_dir: Path | str,
    trace_root: Path | str,
    task_group: str,
    conversation_id: str,
    generation_threshold: int,
    k_init: int,
    k: int,
    freeze: bool,
    worker_model: str | None,
    worker_timeout_seconds: int,
) -> str | None:
    """Idempotently queue missed generation or refinement work."""
    _supersede_legacy_detached_job(workspace)
    common: dict[str, Any] = {
        "store_dir": store_dir,
        "trace_root": trace_root,
        "task_group": task_group,
        "conversation_id": conversation_id,
        "worker_model": worker_model,
        "worker_timeout_seconds": worker_timeout_seconds,
    }
    return _poll_learning_jobs(
        workspace,
        enqueue_job=lambda kind: enqueue_claude_learning_job(
            workspace,
            kind=kind,
            **common,
        ),
        store_dir=store_dir,
        trace_root=trace_root,
        generation_threshold=generation_threshold,
        k_init=k_init,
        k=k,
        freeze=freeze,
    )


def _supersede_legacy_detached_job(workspace: ProgramWorkspace) -> None:
    manifest = workspace.load()
    learning = (
        manifest.get("interactive_learning")
        or manifest.get("codex_learning")
        or {}
    )
    job_id = learning.get("active_job_id")
    if not job_id:
        return
    job_dir = Path(workspace.root) / JOBS_DIR / str(job_id)
    job_path = job_dir / "job.json"
    if not job_path.exists():
        return
    with _job_lock(job_dir):
        job = _read_json(job_path)
        if job.get("state") in TERMINAL_STATES:
            return
        if job.get("dispatch_mode") or job.get("worker_driver") != "claude_subagent":
            return
        _finish_unsuccessful(
            workspace,
            job_dir,
            job,
            "failed",
            "legacy detached Claude worker was superseded by native in-task learning",
        )


def _prepare_job_files(job_dir: Path) -> None:
    from adamast.learning.learning_jobs import _write_json_atomic

    snapshot = _read_json(job_dir / "snapshot.json")
    _write_json_atomic(job_dir / "output.schema.json", candidate_schema())
    write_text_atomic_retry(
        job_dir / "prompt.txt",
        build_prompt(snapshot),
        encoding="utf-8",
    )


__all__ = [
    "LearningJobError",
    "capture_learning_receipt",
    "claim_learning_job",
    "drain_learning_notices",
    "enqueue_claude_learning_job",
    "poll_learning_jobs",
    "reconcile_learning_jobs",
    "resolve_claude_cli",
]
