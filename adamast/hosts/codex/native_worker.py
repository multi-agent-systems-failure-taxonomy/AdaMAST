"""Proposal-only Codex worker for AdaMAST taxonomy learning jobs."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from adamast.learning.learning_jobs import (
    LearningJobError,
    _job_lock,
    _read_json,
    _short_error,
    _write_json_atomic,
)
from adamast.learning.worker_contract import build_prompt, candidate_schema


def build_codex_command(job_dir: Path, job: dict[str, Any]) -> list[str]:
    command = [
        str(job["codex_cli_path"]),
        "--disable",
        "hooks",
        "-a",
        "never",
        "-s",
        "read-only",
        "-C",
        str(job_dir),
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
        "--output-schema",
        str(job_dir / "output.schema.json"),
        "-o",
        str(job_dir / "candidate.json"),
    ]
    model = job.get("worker_model")
    if isinstance(model, str) and model.strip():
        command.extend(["-m", model.strip()])
    command.append("-")
    return command


def run_worker(
    job_dir: Path | str,
    *,
    runner=None,
) -> int:
    """Claim one immutable job, execute Codex, and submit a receipt."""
    job_dir = Path(job_dir).expanduser().resolve()
    job_path = job_dir / "job.json"
    snapshot_path = job_dir / "snapshot.json"
    with _job_lock(job_dir):
        job = _read_json(job_path)
        if job.get("state") != "queued":
            return 0
        job["state"] = "running"
        job["attempts"] = int(job.get("attempts", 0)) + 1
        job["worker_pid"] = os.getpid()
        job["started_at_unix"] = time.time()
        job["updated_at_unix"] = time.time()
        _write_json_atomic(job_path, job)

    snapshot = _read_json(snapshot_path)
    _write_json_atomic(job_dir / "output.schema.json", candidate_schema())
    prompt = build_prompt(snapshot)
    (job_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    command = build_codex_command(job_dir, job)
    run = runner or _run_codex
    try:
        completed = run(
            command,
            prompt=prompt,
            job_dir=job_dir,
            timeout_seconds=int(job.get("worker_timeout_seconds", 1800)),
        )
        returncode = int(getattr(completed, "returncode", completed))
        if returncode != 0:
            raise LearningJobError(f"Codex worker exited with code {returncode}")
        candidate_path = job_dir / "candidate.json"
        if not candidate_path.exists():
            raise LearningJobError("Codex worker returned no candidate")
        candidate = _read_json(candidate_path)
        receipt = {
            "version": 1,
            "job_id": job["job_id"],
            "snapshot_hash": job["snapshot_hash"],
            "status": "candidate",
            "candidate": candidate,
            "completed_at_unix": time.time(),
        }
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 - receipt must preserve worker failure
        receipt = {
            "version": 1,
            "job_id": job["job_id"],
            "snapshot_hash": job["snapshot_hash"],
            "status": "failed",
            "error": _short_error(str(exc)),
            "completed_at_unix": time.time(),
        }
        exit_code = 1

    _write_json_atomic(job_dir / "receipt.json", receipt)
    with _job_lock(job_dir):
        latest = _read_json(job_path)
        if latest.get("state") == "running":
            latest["state"] = "awaiting_reconcile"
            latest["updated_at_unix"] = time.time()
            latest["last_error"] = receipt.get("error")
            _write_json_atomic(job_path, latest)
    return exit_code


# The worker contract is "reuse the signed-in CLI": its persisted login, not
# the spawning session's transport. A session-scoped gateway URL inherited
# from the host conversation misroutes the detached child's credentials. A
# user's own OPENAI_API_KEY is a deliberate credential and stays.
_SESSION_TRANSPORT_VARS = (
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
)


def _run_codex(
    command: list[str],
    *,
    prompt: str,
    job_dir: Path,
    timeout_seconds: int,
):
    env = os.environ.copy()
    for name in _SESSION_TRANSPORT_VARS:
        env.pop(name, None)
    events_path = job_dir / "events.jsonl"
    stderr_path = job_dir / "stderr.log"
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with events_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        # Explicit UTF-8: the default locale codec (cp1252 on Windows) cannot
        # encode prompts containing characters like U+2192, which kills stdin
        # before the CLI reads it.
        return subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="strict",
            stdout=stdout,
            stderr=stderr,
            cwd=job_dir,
            timeout=timeout_seconds,
            check=False,
            creationflags=creationflags,
            env=env,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one AdaMAST Codex learning job.")
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args(argv)
    return run_worker(args.job_dir)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
