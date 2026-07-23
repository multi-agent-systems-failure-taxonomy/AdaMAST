"""Host-neutral claim and receipt protocol for in-task taxonomy subagents."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from adamast.learning.learning_jobs import (
    JOBS_DIR,
    LearningJobError,
    _job_lock,
    _read_json,
    _short_error,
    _sync_job_summary,
    _write_json_atomic,
)
from adamast import ProgramWorkspace

DEFAULT_CLAIM_SECONDS = 1800
RECEIPT_OPEN = "<ADAMAST_TAXONOMY_RECEIPT>"
RECEIPT_CLOSE = "</ADAMAST_TAXONOMY_RECEIPT>"
MAX_RECEIPT_CHARS = 256_000
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def claim_learning_job(
    workspace: ProgramWorkspace,
    *,
    conversation_id: str,
    lease_seconds: int = DEFAULT_CLAIM_SECONDS,
    host_label: str,
    subagent_capability: str,
    forbidden_cli: str,
    expected_host: str | None = None,
    expected_worker_drivers: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Claim the project's queued host job and return a ready-to-spawn task."""
    jobs_root = Path(workspace.root) / JOBS_DIR
    if not jobs_root.exists():
        return None
    manifest = workspace.load()
    active_job_id = (manifest.get("interactive_learning") or {}).get(
        "active_job_id"
    ) or (manifest.get("codex_learning") or {}).get("active_job_id")
    if not active_job_id:
        return None
    job_dir = jobs_root / str(active_job_id)
    job_path = job_dir / "job.json"
    if not job_path.exists():
        return None
    with _job_lock(job_dir):
        job = _read_json(job_path)
        job_host = str(job.get("host") or "").strip().casefold().replace("-", "_")
        normalized_expected_host = (
            str(expected_host or "").strip().casefold().replace("-", "_")
        )
        if job_host and normalized_expected_host and job_host != normalized_expected_host:
            return None
        if (
            expected_worker_drivers
            and job.get("worker_driver") not in expected_worker_drivers
        ):
            return None
        job_conversation = str(job.get("conversation_id") or "").strip()
        if job_conversation and job_conversation != str(conversation_id):
            return None
        branch = manifest.get("branch")
        manifest_branch_id = (
            str(branch.get("branch_id") or "").strip()
            if isinstance(branch, dict)
            else ""
        )
        job_branch_id = str(job.get("branch_id") or "").strip()
        if job_branch_id and job_branch_id != manifest_branch_id:
            return None
        if job.get("dispatch_mode") != "host_subagent":
            return None
        if job.get("state") == "claimed":
            expires_at = float(job.get("claim_expires_at_unix", 0) or 0)
            if not expires_at or time.time() < expires_at:
                return None
            _release_claim(job)
        if job.get("state") not in {"queued", "support_queued"}:
            return None
        phase = "support" if job.get("state") == "support_queued" else "candidate"
        now = time.time()
        token = secrets.token_urlsafe(24)
        job.update(
            state="claimed",
            claim_token=token,
            claimed_by=str(conversation_id),
            claimed_at_unix=now,
            claim_expires_at_unix=now + max(60, int(lease_seconds)),
            claim_phase=phase,
            attempts=int(job.get("attempts", 0)) + 1,
            updated_at_unix=now,
        )
        _write_json_atomic(job_path, job)
    _sync_job_summary(workspace, job)
    return _dispatch(
        job_dir,
        job,
        token,
        host_label=host_label,
        subagent_capability=subagent_capability,
        forbidden_cli=forbidden_cli,
    )


def complete_learning_job(
    job_dir: Path | str,
    *,
    claim_token: str,
    candidate: dict[str, Any],
) -> bool:
    """Submit a proposal receipt; the parent reconciler remains authoritative."""
    job_dir = Path(job_dir).expanduser().resolve()
    job_path = job_dir / "job.json"
    receipt_path = job_dir / "receipt.json"
    with _job_lock(job_dir):
        job = _read_json(job_path)
        if receipt_path.exists() and job.get("state") in {
            "awaiting_reconcile",
            "activating",
            "activated",
            "no_change",
        }:
            if not secrets.compare_digest(
                str(job.get("claim_token") or ""), str(claim_token)
            ):
                raise LearningJobError("learning job claim token mismatch")
            receipt = _read_json(receipt_path)
            return receipt.get("job_id") == job.get("job_id")
        _require_live_claim(job, claim_token, phase="candidate")
        receipt = {
            "version": 1,
            "job_id": job["job_id"],
            "snapshot_hash": job["snapshot_hash"],
            "status": "candidate",
            "candidate": candidate,
            "completed_at_unix": time.time(),
        }
        _write_json_atomic(receipt_path, receipt)
        job["state"] = "awaiting_reconcile"
        job["updated_at_unix"] = time.time()
        job["last_error"] = None
        _write_json_atomic(job_path, job)
    return True


def complete_support_review(
    job_dir: Path | str,
    *,
    claim_token: str,
    review: dict[str, Any],
) -> bool:
    """Submit the independent semantic-support review for a staged candidate."""
    job_dir = Path(job_dir).expanduser().resolve()
    job_path = job_dir / "job.json"
    receipt_path = job_dir / "support_receipt.json"
    with _job_lock(job_dir):
        job = _read_json(job_path)
        if receipt_path.exists() and job.get("state") in {
            "awaiting_support_reconcile",
            "activating",
            "activated",
            "no_change",
        }:
            if not secrets.compare_digest(
                str(job.get("claim_token") or ""), str(claim_token)
            ):
                raise LearningJobError("learning job claim token mismatch")
            receipt = _read_json(receipt_path)
            return receipt.get("job_id") == job.get("job_id")
        _require_live_claim(job, claim_token, phase="support")
        _write_json_atomic(
            receipt_path,
            {
                "version": 1,
                "job_id": job["job_id"],
                "snapshot_hash": job["snapshot_hash"],
                "status": "support_review",
                "review": review,
                "completed_at_unix": time.time(),
            },
        )
        job["state"] = "awaiting_support_reconcile"
        job["updated_at_unix"] = time.time()
        job["last_error"] = None
        _write_json_atomic(job_path, job)
    return True


def fail_learning_job(
    job_dir: Path | str,
    *,
    claim_token: str,
    reason: str,
) -> bool:
    """Submit a failed receipt so normal reconciliation records the outcome."""
    job_dir = Path(job_dir).expanduser().resolve()
    job_path = job_dir / "job.json"
    with _job_lock(job_dir):
        job = _read_json(job_path)
        phase = str(job.get("claim_phase") or "candidate")
        _require_live_claim(job, claim_token, phase=phase)
        support_phase = phase == "support"
        _write_json_atomic(
            job_dir / ("support_receipt.json" if support_phase else "receipt.json"),
            {
                "version": 1,
                "job_id": job["job_id"],
                "snapshot_hash": job["snapshot_hash"],
                "status": "failed",
                "error": _short_error(reason),
                "completed_at_unix": time.time(),
            },
        )
        job["state"] = (
            "awaiting_support_reconcile" if support_phase else "awaiting_reconcile"
        )
        job["updated_at_unix"] = time.time()
        job["last_error"] = _short_error(reason)
        _write_json_atomic(job_path, job)
    return True


def capture_learning_receipt(
    workspace: ProgramWorkspace,
    event: dict[str, Any],
) -> str | None:
    """Capture one taxonomy receipt from a completed native subagent."""
    payload = _receipt_payload(event)
    if payload is None:
        return None
    job_id = str(payload.get("job_id") or "")
    if not _SAFE_JOB_ID.fullmatch(job_id):
        raise LearningJobError("taxonomy receipt has an invalid job id")
    job_dir = Path(workspace.root) / JOBS_DIR / job_id
    status = payload.get("status")
    if status == "candidate":
        complete_learning_job(
            job_dir,
            claim_token=str(payload.get("claim_token") or ""),
            candidate=_receipt_object(payload.get("candidate"), "candidate"),
        )
    elif status == "support_review":
        complete_support_review(
            job_dir,
            claim_token=str(payload.get("claim_token") or ""),
            review=_receipt_object(payload.get("review"), "support review"),
        )
    elif status == "failed":
        fail_learning_job(
            job_dir,
            claim_token=str(payload.get("claim_token") or ""),
            reason=str(payload.get("error") or "taxonomy subagent failed"),
        )
    else:
        raise LearningJobError("taxonomy receipt status must be candidate or failed")
    return job_id


def _dispatch(
    job_dir: Path,
    job: dict[str, Any],
    token: str,
    *,
    host_label: str,
    subagent_capability: str,
    forbidden_cli: str,
) -> dict[str, Any]:
    support_phase = job.get("claim_phase") == "support"
    candidate_envelope = json.dumps(
        {
            "version": 1,
            "job_id": job["job_id"],
            "claim_token": token,
            "status": "candidate",
            "candidate": {
                "decision": "replace",
                "display_name": "<concise taxonomy name>",
                "domain": "<taxonomy domain>",
                "summary": "<taxonomy summary>",
                "codes": [],
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    failed_envelope = json.dumps(
        {
            "version": 1,
            "job_id": job["job_id"],
            "claim_token": token,
            "status": "failed",
            "error": "<concise reason>",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if support_phase:
        support_envelope = json.dumps(
            {
                "version": 1,
                "job_id": job["job_id"],
                "claim_token": token,
                "status": "support_review",
                "review": {
                    "supported": True,
                    "codes": [
                        {
                            "id": "<candidate code id>",
                            "supported": True,
                            "reason": "<evidence-grounded reason>",
                            "trace_ids": ["<cited trace id>"],
                        }
                    ],
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        task_prompt = (
            "You are the independent AdaMAST taxonomy support-review subagent. "
            "Work independently from the generator and the user's main task. Read "
            f'only the UTF-8 prompt at "{job_dir / "support_prompt.txt"}" and '
            f'schema at "{job_dir / "support_output.schema.json"}". Do not browse '
            "the repository, use network access, inspect credentials, edit files, "
            f"or launch {forbidden_cli}. Produce one support review JSON object. "
            "Return only one compact receipt with no Markdown or surrounding text, "
            "replacing the example review with the complete object:\n"
            f"{RECEIPT_OPEN}{support_envelope}{RECEIPT_CLOSE}\n"
            "If the review cannot be completed, return only this receipt with a "
            f"concise error:\n{RECEIPT_OPEN}{failed_envelope}{RECEIPT_CLOSE}"
        )
    else:
        task_prompt = (
            "You are the AdaMAST taxonomy learning subagent for one frozen job. "
            "Work independently from the user's main task. Read only the UTF-8 "
            f'prompt at "{job_dir / "prompt.txt"}" and schema at '
            f'"{job_dir / "output.schema.json"}". Do not browse the repository, '
            "use network access, inspect credentials, edit files, or launch "
            f"{forbidden_cli}. Produce one candidate JSON object that satisfies "
            "the schema. Return only one compact receipt with no Markdown or "
            "surrounding text, replacing the example candidate with the complete "
            f"object:\n{RECEIPT_OPEN}{candidate_envelope}{RECEIPT_CLOSE}\n"
            "If the job cannot be completed, return only this receipt with a "
            f"concise error:\n{RECEIPT_OPEN}{failed_envelope}{RECEIPT_CLOSE}"
        )
    directive = (
        f"AdaMAST native taxonomy learning is ready. Launch one native {host_label} "
        "subagent now with the task prompt below as a background task, while "
        "continuing the user's main work immediately. "
        f"Use {subagent_capability}, not {forbidden_cli}. "
        "Do not generate the taxonomy in the main agent and do not ask the user for "
        "an API key. Do not wait, join, poll, or delay the user's response for this "
        "worker; AdaMAST will reconcile its receipt on a later lifecycle event.\n\n"
        f"Job: {job['job_id']} ({job['kind']}, "
        f"{'support review' if support_phase else 'candidate generation'})\n"
        "SUBAGENT TASK BEGIN\n"
        f"{task_prompt}\n"
        "SUBAGENT TASK END"
    )
    return {
        "job_id": job["job_id"],
        "claim_token": token,
        "job_dir": str(job_dir),
        "task_name": (
            f"adamast_{job['kind']}_support"
            if support_phase
            else f"adamast_{job['kind']}"
        ),
        "task_prompt": task_prompt,
        "directive": directive,
        "background_required": True,
    }


def _receipt_object(value: Any, label: str) -> dict[str, Any]:
    """Accept a JSON object or one legacy receipt layer containing that object."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise LearningJobError(f"{label} must be an object") from exc
        if isinstance(decoded, dict):
            return decoded
    raise LearningJobError(f"{label} must be an object")


def _receipt_payload(event: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    for key in (
        "last_assistant_message",
        "assistant_message",
        "message",
        "text",
    ):
        value = event.get(key)
        if isinstance(value, str):
            candidates.append(value)
    transcript = event.get("agent_transcript_path")
    if transcript:
        candidates.extend(_transcript_strings(Path(str(transcript))))
    for text in reversed(candidates):
        start = text.rfind(RECEIPT_OPEN)
        end = text.rfind(RECEIPT_CLOSE)
        if start < 0 or end <= start:
            continue
        raw = text[start + len(RECEIPT_OPEN) : end]
        if len(raw) > MAX_RECEIPT_CHARS:
            raise LearningJobError("taxonomy receipt exceeds the size limit")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _transcript_strings(path: Path) -> list[str]:
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            handle.seek(max(0, size - 4_000_000))
            if size > 4_000_000:
                handle.readline()
            raw = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    strings: list[str] = []
    for line in raw.splitlines()[-128:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        strings.extend(_assistant_strings(item))
    return strings


def _assistant_strings(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return []
    payload = item.get("payload")
    if item.get("type") == "event_msg" and isinstance(payload, dict):
        if payload.get("type") == "agent_message":
            return _nested_strings(payload.get("message"))
        return []
    if item.get("type") == "response_item" and isinstance(payload, dict):
        if payload.get("type") == "message" and payload.get("role") == "assistant":
            return _nested_strings(payload.get("content"))
        return []
    if item.get("role") == "assistant":
        return _nested_strings(item)
    if item.get("type") == "assistant":
        return _nested_strings(item.get("message") or item)
    return []


def _nested_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _nested_strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _nested_strings(item)]
    return []


def _require_live_claim(
    job: dict[str, Any],
    token: str,
    *,
    phase: str,
) -> None:
    if job.get("dispatch_mode") != "host_subagent":
        raise LearningJobError("job is not assigned to a host-subagent path")
    if job.get("state") != "claimed":
        raise LearningJobError("learning job is not currently claimed")
    if job.get("claim_phase") != phase:
        raise LearningJobError(f"learning job is not claimed for {phase}")
    if not secrets.compare_digest(str(job.get("claim_token") or ""), str(token)):
        raise LearningJobError("learning job claim token mismatch")
    expires_at = float(job.get("claim_expires_at_unix", 0) or 0)
    if not expires_at or time.time() >= expires_at:
        raise LearningJobError("learning job claim has expired")


def _release_claim(job: dict[str, Any]) -> None:
    job["state"] = (
        "support_queued" if job.get("claim_phase") == "support" else "queued"
    )
    for key in (
        "claim_token",
        "claimed_by",
        "claimed_at_unix",
        "claim_expires_at_unix",
        "claim_phase",
    ):
        job.pop(key, None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AdaMAST subagent receipt protocol")
    subparsers = parser.add_subparsers(dest="command", required=True)
    complete = subparsers.add_parser("complete")
    complete.add_argument("--job-dir", required=True)
    complete.add_argument("--claim-token", required=True)
    complete.add_argument("--candidate", required=True)
    failed = subparsers.add_parser("fail")
    failed.add_argument("--job-dir", required=True)
    failed.add_argument("--claim-token", required=True)
    failed.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "complete":
            candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
            complete_learning_job(
                args.job_dir,
                claim_token=args.claim_token,
                candidate=candidate,
            )
        else:
            fail_learning_job(
                args.job_dir,
                claim_token=args.claim_token,
                reason=args.reason,
            )
    except (OSError, ValueError, json.JSONDecodeError, LearningJobError) as exc:
        print(f"AdaMAST taxonomy receipt failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "command": args.command}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
