"""Codex-native learning jobs built on the shared durable coordinator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    resolve_codex_cli,
    validate_candidate,
)
from adamast.learning.worker_contract import build_prompt, candidate_schema
from adamast.core.fsio import write_text_atomic_retry

from .subagent_protocol import capture_learning_receipt, claim_learning_job

__all__ = [
    "LearningJobError",
    "claim_learning_job",
    "capture_learning_receipt",
    "drain_learning_notices",
    "enqueue_learning_job",
    "poll_learning_jobs",
    "reconcile_learning_jobs",
    "resolve_codex_cli",
    "validate_candidate",
]


def enqueue_learning_job(workspace, **kwargs: Any) -> str:
    """Queue a job for a native subagent in the current Codex task."""
    if kwargs.get("launcher") is not None or kwargs.get("codex_cli_path") is not None:
        # Preserve the documented low-level test/compatibility seam. The Codex
        # runtime itself never supplies these fields.
        return _enqueue_learning_job(workspace, **kwargs)
    ignored_legacy_fields = ("codex_cli_path", "worker_cli_path", "worker_module")
    for field in ignored_legacy_fields:
        kwargs.pop(field, None)
    kwargs["worker_model"] = None
    job_id = _enqueue_learning_job(
        workspace,
        **kwargs,
        worker_driver="codex_native_subagent",
        worker_label="Codex in-task taxonomy subagent",
        worker_module="adamast.hosts.codex.subagent_protocol",
        job_prefix="codex-native",
        dispatch_mode="host_subagent",
    )
    job_dir = Path(workspace.root) / "learning_jobs" / job_id
    _prepare_job_files(job_dir)
    return job_id


def poll_learning_jobs(
    workspace,
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
    """Idempotently repair a missed generation or refinement trigger."""
    _supersede_legacy_detached_job(workspace)
    common = {
        "store_dir": store_dir,
        "trace_root": trace_root,
        "task_group": task_group,
        "conversation_id": conversation_id,
        "worker_model": worker_model,
        "worker_timeout_seconds": worker_timeout_seconds,
    }
    return _poll_learning_jobs(
        workspace,
        enqueue_job=lambda kind: enqueue_learning_job(
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


def _supersede_legacy_detached_job(workspace) -> None:
    """Retire a pre-native Codex job so polling can queue the new transport."""
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
        if job.get("dispatch_mode") or job.get("worker_driver") != "codex_subagent":
            return
        _finish_unsuccessful(
            workspace,
            job_dir,
            job,
            "failed",
            "legacy detached Codex worker was superseded by native in-task learning",
        )


def _prepare_job_files(job_dir: Path) -> None:
    from adamast.learning.learning_jobs import (
        _read_json,
        _write_json_atomic,
    )

    snapshot = _read_json(job_dir / "snapshot.json")
    _write_json_atomic(job_dir / "output.schema.json", candidate_schema())
    write_text_atomic_retry(
        job_dir / "prompt.txt",
        build_prompt(snapshot),
        encoding="utf-8",
    )
