"""Program/branch-local refinement cadence with one-to-many lineage edges."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from string import Template
from typing import Any, Callable

from adamast.core import store

from .generation import Approver
from adamast.llm.learning_calls import (
    build_refinement_prompt,
    format_refinement_traces,
    parse_json_object,
    refine_json,
)
from adamast.core.lineage import TaxonomyLineage
from adamast.core.program import ProgramWorkspace
from adamast.core.traces import DEFAULT_TRACE_ROOT, GenerationTrace
from adamast.core.worker_state import (
    REFINEMENT_WORKER_STATE,
    WorkerHeartbeat,
    write_worker_state,
)

DEFAULT_K_INIT = 10
DEFAULT_K = 20
DEFAULT_OVERLAP_THRESHOLD = 0.72
Refiner = Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]]
RefinementJudge = Callable[
    [
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        list[dict[str, Any]],
        str,
    ],
    Any,
]
RefinementRepairer = Callable[
    [
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        list[dict[str, Any]],
        list[Any],
        str,
    ],
    dict[str, Any],
]


@dataclass(frozen=True)
class RefinementResult:
    action: str
    reason: str
    taxonomy_id: str | None = None


def refinement_threshold(
    workspace: ProgramWorkspace,
    *,
    k_init: int = DEFAULT_K_INIT,
    k: int = DEFAULT_K,
) -> int:
    if k_init <= 0 or k <= 0:
        raise ValueError("K_init and K must be positive")
    rounds = int(workspace.refinement_state().get("rounds_completed", 0))
    return k_init if rounds == 0 else k


def trigger_refinement(
    workspace: ProgramWorkspace,
    *,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    trace_root: Path | str = DEFAULT_TRACE_ROOT,
    k_init: int = DEFAULT_K_INIT,
    k: int = DEFAULT_K,
    refinement_stops: bool = False,
    advanced_refinement: bool = False,
    adamast_model: str | None = None,
    refiner: Refiner | None = None,
    approver: Approver | None = None,
    judge: RefinementJudge | None = None,
    repairer: RefinementRepairer | None = None,
    background_launcher: Callable[[], None] | None = None,
) -> RefinementResult:
    state = workspace.refinement_state()
    threshold = refinement_threshold(workspace, k_init=k_init, k=k)
    count = int(state.get("traces_since_refinement", 0))
    if count < threshold:
        return RefinementResult(
            "none",
            f"refinement threshold not reached: {count}/{threshold}",
        )
    worker_kind = "inline" if refinement_stops else "background"
    if not workspace.try_begin_refinement(threshold, worker_kind=worker_kind):
        return RefinementResult("none", "refinement already running or unnecessary")

    if refinement_stops:
        return run_refinement_job(
            workspace,
            store_dir=store_dir,
            trace_root=trace_root,
            advanced_refinement=advanced_refinement,
            adamast_model=adamast_model,
            refiner=refiner,
            approver=approver,
            judge=judge,
            repairer=repairer,
        )

    try:
        if background_launcher is not None:
            background_launcher()
        else:
            _spawn_worker(
                workspace.root,
                Path(store_dir),
                Path(trace_root),
                advanced_refinement=advanced_refinement,
                adamast_model=adamast_model,
            )
    except Exception as exc:
        workspace.mark_refinement("failed", str(exc))
        return RefinementResult("failed", f"could not start refinement: {exc}")
    return RefinementResult(
        "started",
        "refinement started in background; current taxonomy remains active",
    )


def run_refinement_job(
    workspace: ProgramWorkspace,
    *,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    trace_root: Path | str = DEFAULT_TRACE_ROOT,
    advanced_refinement: bool = False,
    adamast_model: str | None = None,
    refiner: Refiner | None = None,
    approver: Approver | None = None,
    judge: RefinementJudge | None = None,
    repairer: RefinementRepairer | None = None,
    activation_poll_seconds: float = 0.05,
    activation_timeout_seconds: float = 86_400,
) -> RefinementResult:
    model = adamast_model or workspace.load().get("adamast_model")
    refine = refiner or (
        (lambda current, traces: _model_refiner(current, traces, str(model)))
        if model
        else _unconfigured_refiner
    )
    accept = approver or structurally_accept_refinement
    store_dir = Path(store_dir)
    trace_root = Path(trace_root)
    try:
        current_id = workspace.load().get("taxonomy_id")
        if not current_id:
            raise ValueError("program has no taxonomy to refine")
        current_id = str(current_id)
        current = store.fetch_by_id(current_id, store_dir)
        traces = _load_program_refinement_traces(workspace, trace_root)
        trace_records = [trace.to_dict() for trace in traces]
        candidate = refine(current, trace_records)
        workspace.record_usage_event(
            stage="taxonomy_refinement",
            model=str(model) if model else None,
            usage_available=False,
            details={
                "trace_count": len(trace_records),
                "custom_refiner": refiner is not None,
                "note": "transport did not expose token or cost metadata",
            },
        )
        candidate = _normalize_candidate(candidate, current)
        if not structurally_accept_refinement(candidate):
            raise ValueError("refinement candidate failed structural validation")
        diff = structural_diff(current, candidate)
        issues: list[Any] = []
        repaired = False
        if advanced_refinement:
            if not model:
                raise ValueError("adamast_model is required for advanced refinement")
            review = judge or _model_refinement_judge
            issues = _normalize_issues(
                review(current, candidate, diff, trace_records, str(model))
            )
            workspace.record_usage_event(
                stage="refinement_support_judge",
                model=str(model),
                usage_available=False,
                details={
                    "trace_count": len(trace_records),
                    "issue_count": len(issues),
                    "custom_judge": judge is not None,
                    "note": "transport did not expose token or cost metadata",
                },
            )
            if issues:
                revise = repairer or _model_refinement_repairer
                candidate = revise(
                    current,
                    candidate,
                    diff,
                    trace_records,
                    issues,
                    str(model),
                )
                workspace.record_usage_event(
                    stage="refinement_repair",
                    model=str(model),
                    usage_available=False,
                    details={
                        "trace_count": len(trace_records),
                        "issue_count": len(issues),
                        "custom_repairer": repairer is not None,
                        "note": "transport did not expose token or cost metadata",
                    },
                )
                candidate = _normalize_candidate(candidate, current)
                if not structurally_accept_refinement(candidate):
                    raise ValueError(
                        "judge-guided refinement repair failed structural validation"
                    )
                diff = structural_diff(current, candidate)
                repaired = True
        overlap_warnings = overlap_lint(candidate)
        if not accept(candidate):
            workspace.mark_refinement("rejected", "candidate rejected")
            return RefinementResult(
                "rejected",
                "refined candidate was rejected; counter and current taxonomy preserved",
            )
        taxonomy_id = _wait_and_commit(
            workspace,
            current_id,
            candidate,
            diff=diff,
            advanced_refinement=advanced_refinement,
            repaired=repaired,
            judge_issues=issues,
            overlap_warnings=overlap_warnings,
            store_dir=store_dir,
            trace_root=trace_root,
            poll_seconds=activation_poll_seconds,
            timeout_seconds=activation_timeout_seconds,
        )
        return RefinementResult(
            "activated",
            "refined taxonomy approved and activated",
            taxonomy_id,
        )
    except Exception as exc:
        workspace.mark_refinement("failed", str(exc))
        return RefinementResult(
            "failed",
            f"refinement failed; current taxonomy and counter preserved: {exc}",
        )
    finally:
        try:
            from adamast.dashboard.server import stop_dashboard_if_idle

            stop_dashboard_if_idle(workspace)
        except Exception:
            pass


def _unconfigured_refiner(
    _current: dict[str, Any],
    _traces: list[dict[str, Any]],
) -> dict[str, Any]:
    raise RuntimeError("no refinement model/call has been configured")


def structurally_accept_refinement(candidate: dict[str, Any]) -> bool:
    """Accept a complete replacement candidate with canonical, id-bearing codes."""
    if not isinstance(candidate, dict):
        return False
    codes = candidate.get("codes")
    if not (
        isinstance(candidate.get("repo"), str)
        and isinstance(candidate.get("domain"), str)
        and isinstance(codes, list)
        and bool(codes)
        and "taxonomy_id" not in candidate
    ):
        return False
    return all(
        isinstance(code, dict)
        and isinstance(code.get("id"), str)
        and bool(code["id"].strip())
        for code in codes
    )


def structural_diff(
    current: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Return a deterministic, deliberately small replacement diff."""
    before = {_code_identity(code): code for code in current.get("codes", [])}
    after = {_code_identity(code): code for code in candidate.get("codes", [])}
    shared = sorted(before.keys() & after.keys())
    removed = sorted(before.keys() - after.keys())
    added = sorted(after.keys() - before.keys())
    return {
        "repo_changed": current.get("repo") != candidate.get("repo"),
        "domain_changed": current.get("domain") != candidate.get("domain"),
        "codes_added": added,
        "codes_removed": removed,
        "codes_changed": [
            code_id
            for code_id in shared
            if before[code_id] != after[code_id]
        ],
        "code_id_mapping": {
            "old_to_new": {
                **{code_id: code_id for code_id in shared},
                **{code_id: None for code_id in removed},
            },
            "new_from_old": {
                **{code_id: code_id for code_id in shared},
                **{code_id: None for code_id in added},
            },
        },
    }


def overlap_lint(
    taxonomy: dict[str, Any],
    *,
    threshold: float = DEFAULT_OVERLAP_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return non-blocking warnings for near-duplicate failure modes."""
    codes = [
        code for code in taxonomy.get("codes", [])
        if isinstance(code, dict)
    ]
    warnings: list[dict[str, Any]] = []
    for left_index, left in enumerate(codes):
        left_terms = _lint_terms(left)
        if not left_terms:
            continue
        for right in codes[left_index + 1:]:
            right_terms = _lint_terms(right)
            if not right_terms:
                continue
            overlap = len(left_terms & right_terms) / len(left_terms | right_terms)
            if overlap >= threshold:
                warnings.append(
                    {
                        "code_a": str(left.get("id", "")),
                        "code_b": str(right.get("id", "")),
                        "score": round(overlap, 3),
                        "threshold": threshold,
                        "reason": (
                            "possible overlap between failure-mode names "
                            "or descriptions"
                        ),
                    }
                )
    return warnings


def _lint_terms(code: dict[str, Any]) -> set[str]:
    text = " ".join(
        str(code.get(key, "")) for key in ("name", "description", "category")
    ).lower()
    stopwords = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "in", "into", "is", "it", "its", "of", "on", "or", "that", "the",
        "their", "this", "to", "with", "without", "when",
    }
    return {
        term
        for term in re.findall(r"[a-z0-9]+", text)
        if len(term) > 2 and term not in stopwords
    }


def _code_identity(code: dict[str, Any]) -> str:
    """Canonical code identity. Codes are validated id-bearing before this runs."""
    return str(code["id"])


def _load_program_refinement_traces(
    workspace: ProgramWorkspace,
    trace_root: Path,
) -> list[GenerationTrace]:
    traces: list[GenerationTrace] = []
    for ref in workspace.refinement_state().get("trace_refs", []):
        path = trace_root / ref["taxonomy_id"] / ref["filename"]
        if not path.is_file():
            raise FileNotFoundError(f"refinement trace is missing: {path}")
        traces.append(
            GenerationTrace.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        )
    if not traces:
        raise ValueError("no program-specific traces available for refinement")
    return traces


def _normalize_candidate(
    candidate: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("refiner must return a taxonomy object")
    normalized = {
        "repo": candidate.get("repo", current.get("repo", "")),
        "domain": candidate.get("domain", current.get("domain", "")),
        "codes": candidate.get("codes"),
    }
    if "taxonomy_id" in candidate:
        raise ValueError("refinement candidate must not allocate taxonomy_id")
    return normalized


def _wait_and_commit(
    workspace: ProgramWorkspace,
    old_id: str,
    candidate: dict[str, Any],
    *,
    diff: dict[str, Any],
    advanced_refinement: bool,
    repaired: bool,
    judge_issues: list[Any],
    overlap_warnings: list[dict[str, Any]],
    store_dir: Path,
    trace_root: Path,
    poll_seconds: float,
    timeout_seconds: float,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while True:
        workspace.reconcile_stale_sessions()
        with workspace.locked_manifest() as manifest:
            if not manifest.get("active_sessions"):
                return _commit_refinement(
                    workspace,
                    manifest,
                    old_id,
                    candidate,
                    diff=diff,
                    advanced_refinement=advanced_refinement,
                    repaired=repaired,
                    judge_issues=judge_issues,
                    overlap_warnings=overlap_warnings,
                    store_dir=store_dir,
                    trace_root=trace_root,
                )
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting for running tasks to finish")
        time.sleep(poll_seconds)


def _commit_refinement(
    workspace: ProgramWorkspace,
    manifest: dict[str, Any],
    old_id: str,
    candidate: dict[str, Any],
    *,
    diff: dict[str, Any],
    advanced_refinement: bool,
    repaired: bool,
    judge_issues: list[Any],
    overlap_warnings: list[dict[str, Any]],
    store_dir: Path,
    trace_root: Path,
) -> str:
    taxonomy_id = _new_taxonomy_id(candidate)
    branch = manifest.get("branch") if isinstance(manifest, dict) else None
    record = {
        "taxonomy_id": taxonomy_id,
        "parent_taxonomy_id": old_id,
        "originating_branch_id": (
            str(branch.get("branch_id"))
            if isinstance(branch, dict) and branch.get("branch_id")
            else None
        ),
        **candidate,
    }
    folder = trace_root / taxonomy_id
    lineage = TaxonomyLineage(store_dir)
    artifact = store_dir / "_state" / "refinements" / f"{taxonomy_id}.json"
    registered = linked = created = artifact_written = False
    try:
        folder.mkdir(parents=True, exist_ok=False)
        created = True
        store.register(record, store_dir)
        registered = True
        lineage.add_successor(
            old_id,
            taxonomy_id,
            branch_id=(
                str(branch.get("branch_id"))
                if isinstance(branch, dict) and branch.get("branch_id")
                else None
            ),
        )
        linked = True
        _write_refinement_artifact(
            artifact,
            old_id=old_id,
            new_id=taxonomy_id,
            diff=diff,
            advanced_refinement=advanced_refinement,
            repaired=repaired,
            judge_issues=judge_issues,
            overlap_warnings=overlap_warnings,
        )
        artifact_written = True

        manifest["taxonomy_id"] = taxonomy_id
        if isinstance(manifest.get("branch"), dict):
            manifest["branch"]["head_taxonomy_id"] = taxonomy_id
        refinement = manifest["refinement"]
        refinement["rounds_completed"] = int(
            refinement.get("rounds_completed", 0)
        ) + 1
        refinement["traces_since_refinement"] = 0
        refinement["trace_refs"] = []
        refinement["state"] = "complete"
        refinement["last_error"] = None
    except Exception:
        if artifact_written:
            artifact.unlink(missing_ok=True)
        if linked:
            lineage.remove_successor(old_id, taxonomy_id)
        if registered:
            store.unregister(taxonomy_id, store_dir)
        if created:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    return taxonomy_id


def _write_refinement_artifact(
    target: Path,
    *,
    old_id: str,
    new_id: str,
    diff: dict[str, Any],
    advanced_refinement: bool,
    repaired: bool,
    judge_issues: list[Any],
    overlap_warnings: list[dict[str, Any]],
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "from_taxonomy_id": old_id,
                "to_taxonomy_id": new_id,
                "advanced_refinement": advanced_refinement,
                "repaired": repaired,
                "judge_issues": judge_issues,
                "overlap_warnings": overlap_warnings,
                "diff": diff,
            },
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def _new_taxonomy_id(candidate: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(
        json.dumps(candidate, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]
    return f"tax-{stamp}-{digest}-{uuid.uuid4().hex[:6]}"


def _normalize_issues(value: Any) -> list[Any]:
    issues: list[Any]
    if value is None:
        issues = []
    elif isinstance(value, list):
        issues = value
    elif isinstance(value, dict) and isinstance(value.get("issues"), list):
        issues = value["issues"]
    elif isinstance(value, str):
        parsed = _parse_json_object(value)
        if isinstance(parsed, dict) and isinstance(parsed.get("issues"), list):
            issues = parsed["issues"]
        else:
            raise ValueError(
                "refinement judge must return a list or {'issues': [...]} object"
            )
    else:
        raise ValueError(
            "refinement judge must return a list or {'issues': [...]} object"
        )
    try:
        return json.loads(json.dumps(issues, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("refinement judge issues must be JSON-serializable") from exc


def _model_refiner(
    current: dict[str, Any],
    traces: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    prompt = build_refinement_prompt(current, traces)
    return _model_json(prompt, model, "refinement model")


def _model_refinement_judge(
    current: dict[str, Any],
    candidate: dict[str, Any],
    diff: dict[str, Any],
    traces: list[dict[str, Any]],
    model: str,
) -> Any:
    prompt = _render_refinement_asset(
        "refinement_support_judge.md",
        current=json.dumps(current, ensure_ascii=False),
        candidate=json.dumps(candidate, ensure_ascii=False),
        diff=json.dumps(diff, ensure_ascii=False),
        traces=format_refinement_traces(traces),
    )
    return _model_json(prompt, model, "refinement judge")


def _model_refinement_repairer(
    current: dict[str, Any],
    candidate: dict[str, Any],
    diff: dict[str, Any],
    traces: list[dict[str, Any]],
    issues: list[Any],
    model: str,
) -> dict[str, Any]:
    prompt = _render_refinement_asset(
        "refinement_repair.md",
        current=json.dumps(current, ensure_ascii=False),
        candidate=json.dumps(candidate, ensure_ascii=False),
        diff=json.dumps(diff, ensure_ascii=False),
        issues=json.dumps(issues, ensure_ascii=False),
        traces=format_refinement_traces(traces),
    )
    return _model_json(prompt, model, "refinement repair model")


def _render_refinement_asset(name: str, **context: str) -> str:
    template = (
        files("adamast.learning")
        .joinpath("assets").joinpath(name)
        .read_text(encoding="utf-8")
    )
    return Template(template).substitute(context)


def _model_json(prompt: str, model: str, role: str) -> dict[str, Any]:
    parsed = refine_json(prompt, model=model)
    if parsed is None:
        raise ValueError(f"{role} returned invalid JSON")
    return parsed


def _parse_json_object(raw: Any) -> dict[str, Any] | None:
    return parse_json_object(raw)


def _spawn_worker(
    trace_output: Path,
    store_dir: Path,
    trace_root: Path,
    *,
    advanced_refinement: bool,
    adamast_model: str | None,
) -> None:
    command = [
        sys.executable,
        "-m",
        "adamast.learning.refinement",
        "--worker",
        "--trace-output",
        str(trace_output),
        "--store-dir",
        str(store_dir),
        "--trace-root",
        str(trace_root),
    ]
    if advanced_refinement:
        command.append("--advanced-refinement")
    if adamast_model:
        command.extend(["--adamast-model", adamast_model])
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(Path(__file__).resolve().parents[2]),
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    write_worker_state(
        Path(trace_output) / REFINEMENT_WORKER_STATE,
        "refinement",
        pid=process.pid,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--trace-output", required=True)
    parser.add_argument("--store-dir", required=True)
    parser.add_argument("--trace-root", required=True)
    parser.add_argument("--advanced-refinement", action="store_true")
    parser.add_argument("--adamast-model")
    args = parser.parse_args()
    if not args.worker:
        parser.error("--worker is required")
    workspace = ProgramWorkspace(args.trace_output)
    with WorkerHeartbeat(workspace.root / REFINEMENT_WORKER_STATE, "refinement"):
        result = run_refinement_job(
            workspace,
            store_dir=args.store_dir,
            trace_root=args.trace_root,
            advanced_refinement=args.advanced_refinement,
            adamast_model=args.adamast_model,
        )
    return 0 if result.action == "activated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
