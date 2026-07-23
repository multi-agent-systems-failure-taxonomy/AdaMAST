"""Initial MAST-to-generated-taxonomy learning transition.

Generation starts after N program warm-up traces (default 5). A generated
candidate has no taxonomy_id. Only an accepted candidate is assigned an id,
registered, given a trace folder, and activated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from adamast.core import store

from adamast.core.program import ProgramWorkspace
from adamast.llm.learning_calls import outcome_blind_trace
from .reflection_refinement import refine_with_reflection_judge
from adamast.core.traces import DEFAULT_TRACE_ROOT, TraceStore
from adamast.core.worker_state import (
    GENERATION_WORKER_STATE,
    WorkerHeartbeat,
    write_worker_state,
)

DEFAULT_GENERATION_THRESHOLD = 5
DEFAULT_CATEGORIES: tuple[str, ...] = ("A", "B", "C")
Generator = Callable[[list[dict[str, Any]]], dict[str, Any]]
Approver = Callable[[dict[str, Any]], bool]
JudgeCall = Callable[..., Any]

# Single source of truth for the per-trace projection contract — see
# ``adamast/__init__.py``. Re-exported here for code that imports
# from the generation module directly. Convention: dict in, dict out.
# String-only variants are explicitly disallowed; if you need to rewrite
# only the raw_trajectory text, do it inside the dict and return the dict.
from adamast import ProjectFn  # noqa: E402


@dataclass(frozen=True)
class GenerationResult:
    action: str
    reason: str
    taxonomy_id: str | None = None


def _generation_output_dir(workspace: ProgramWorkspace) -> Path:
    """Vendored-pipeline scratch output, kept inside the program's own root.

    The pipeline owns one program per directory and generation is single-
    flighted, so a stable subdirectory is safe and cannot collide with another
    program (each program has a distinct root).
    """
    return workspace.root / "generation"


def _adamast_generate(
    traces: list[dict[str, Any]],
    adamast_model: str,
    output_dir: Path,
    *,
    project_fn: ProjectFn | None = None,
    seed_roles: dict[str, dict[str, Any]] | None = None,
    categories: Sequence[str] = DEFAULT_CATEGORIES,
    max_codes: int = 0,
) -> dict[str, Any]:
    """Call the vendored AdaMAST pipeline at its public generation boundary.

    This wrapper preserves the pipeline's generation semantics while adapting
    it to adamast's runtime contract:

    - ``project_fn`` rewrites each trace dict before generation (oracle-blind
      projection). Defaults to ``outcome_blind_trace``, which strips outcome
      metadata while keeping the canonical generation fields. Pass a custom
      callable to add additional projection (e.g. summarization, redaction).
    - ``seed_roles`` declares each agent role and the trace step names it
      owns. Required for B-codes; without it, the trace-structure extractor
      has no role labels and B is effectively skipped.
    - ``categories`` controls which axes the pipeline generates. Default
      ``("A","B","C")``; pass ``("A","C")`` when no role schema is available.
    - ``max_codes`` caps the total number of codes (0 = no cap).

    The vendored pipeline always writes ``taxonomy.json`` plus a timestamped
    copy to its output_dir (pipeline.py), regardless of ``save_intermediate``.
    Passing an explicit directory under the program's own root keeps those
    files inside the program's owned space and out of the worker's CWD —
    the vendored default (``resolve_output_dir(None)`` -> ``cwd/adamast_output``)
    is never reached.
    """
    from adamast.learning.vendor import generate_taxonomy

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    project = project_fn or outcome_blind_trace
    projected: list[dict[str, Any]] = []
    for trace in traces:
        out = project(trace)
        if not isinstance(out, dict):
            raise TypeError(
                f"project_fn returned {type(out).__name__} for trace "
                f"{trace.get('problem_id')!r}; must return a dict. "
                f"(string-shape project_fn is explicitly disallowed — "
                f"rewrite raw_trajectory inside the dict and return the dict)"
            )
        projected.append(out)

    # Build a PipelineConfig only when caller actually supplied advanced fields
    # — the vendored PipelineConfig may not accept ``categories`` / ``seed_roles``
    # (older snapshot than upstream AdaMAST). Falling back to the keyword-arg path
    # keeps the default flow byte-compatible with the previous implementation.
    cats = tuple(c.strip().upper() for c in categories if c and c.strip())
    advanced = bool(seed_roles) or cats != DEFAULT_CATEGORIES or max_codes > 0
    if advanced:
        from adamast.learning.vendor.config import PipelineConfig

        config_kwargs: dict[str, Any] = {"model": adamast_model}
        if max_codes:
            config_kwargs["max_codes"] = max_codes
        # These optional fields only exist on newer PipelineConfigs; pass them
        # iff the vendored version accepts them so older snapshots still work
        # (silent no-op rather than a hard TypeError).
        try:
            from inspect import signature

            accepted = set(signature(PipelineConfig).parameters)
        except (TypeError, ValueError):
            accepted = set()
        if "categories" in accepted and cats != DEFAULT_CATEGORIES:
            config_kwargs["categories"] = cats
        if "seed_roles" in accepted and seed_roles:
            config_kwargs["seed_roles"] = dict(seed_roles) if "B" in cats else {}
        config = PipelineConfig(**config_kwargs)
        return generate_taxonomy(
            traces=projected,
            output_dir=output_dir,
            config=config,
            save_intermediate=True,
            verbose=False,
        )

    return generate_taxonomy(
        traces=projected,
        output_dir=output_dir,
        model=adamast_model,
        save_intermediate=True,
        verbose=False,
    )


def candidate_from_adamast(
    raw: dict[str, Any],
    *,
    repo: str = "",
) -> dict[str, Any]:
    """Convert AdaMAST output into id-less codes for the flat taxonomy schema."""
    layer = raw.get("annotation_layer")
    if not isinstance(layer, dict):
        raise ValueError("AdaMAST output has no annotation_layer object")
    codes: list[dict[str, Any]] = []
    for key, entries in layer.items():
        if not key.startswith("category_") or not isinstance(entries, list):
            continue
        # Canonical code `category` is the SHORT label (A/B/C), matching how
        # MAST uses short categories. The verbose A/B/C definitions stay in the
        # vendored output's category_definitions if ever needed.
        category = key.removeprefix("category_").upper()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            code_id = entry.get("code") or entry.get("id")
            name = entry.get("name")
            description = entry.get("definition") or entry.get("description")
            if not all(isinstance(value, str) and value for value in
                       (code_id, name, description)):
                continue
            code = {
                "id": code_id,
                "name": name,
                "description": description,
                "category": category,
            }
            for extra in ("severity", "applies_to_role"):
                if extra in entry:
                    code[extra] = entry[extra]
            codes.append(code)
    if not codes:
        raise ValueError("AdaMAST output produced no usable failure modes")
    return {
        "repo": repo,
        "domain": _domain_name_from_adamast(raw),
        "codes": codes,
    }


def _domain_name_from_adamast(raw: dict[str, Any]) -> str:
    """Keep the display-only domain discovered by AdaMAST pipeline Step 1."""
    full_layer = raw.get("full_layer")
    if not isinstance(full_layer, dict):
        return ""
    domain_info = full_layer.get("domain_info")
    if not isinstance(domain_info, dict):
        return ""
    domain = domain_info.get("domain")
    if not isinstance(domain, dict):
        return ""
    name = domain.get("name")
    return name.strip() if isinstance(name, str) else ""


def structurally_accept(candidate: dict[str, Any]) -> bool:
    """Temporary acceptance seam; quality judgment is intentionally future work."""
    return (
        isinstance(candidate, dict)
        and isinstance(candidate.get("repo"), str)
        and isinstance(candidate.get("domain"), str)
        and isinstance(candidate.get("codes"), list)
        and bool(candidate["codes"])
    )


def trigger_generation(
    workspace: ProgramWorkspace,
    *,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    trace_root: Path | str = DEFAULT_TRACE_ROOT,
    threshold: int = DEFAULT_GENERATION_THRESHOLD,
    generation_stops: bool = False,
    generator: Generator | None = None,
    approver: Approver | None = None,
    adamast_model: str | None = None,
    skip_judge: bool = False,
    judge_call: JudgeCall | None = None,
    refiner_call: Callable[..., Any] | None = None,
    background_launcher: Callable[[], None] | None = None,
) -> GenerationResult:
    """Start generation when the MAST warm-up threshold is crossed.

    When ``skip_judge=False`` (default), generated candidates pass through the
    Reflection-Judge refinement: the judge runs on the same traces the
    taxonomy was induced from, and the refiner applies add / edit / split /
    retire mutations. The post-refinement candidate is then registered and
    activated.

    When ``skip_judge=True``, the candidate skips refinement and is accepted
    on structural validity alone.

    ``judge_call`` / ``refiner_call`` are LLM-transport injection points for
    tests (matching ``AdaMASTReflectionJudge``'s LLMCall signature and
    ``learning_calls.refine_json``'s call kwarg, respectively).
    """
    count = workspace.pending.count()
    retry_after = workspace.generation_retry_after(threshold)
    if count < retry_after:
        return GenerationResult(
            "none",
            f"generation threshold not reached: {count}/{retry_after}",
        )
    worker_kind = "inline" if generation_stops else "background"
    if not workspace.try_begin_generation(worker_kind=worker_kind):
        return GenerationResult("none", "generation already running or unnecessary")

    if generation_stops:
        return run_generation_job(
            workspace,
            store_dir=store_dir,
            trace_root=trace_root,
            generator=generator,
            approver=approver,
            adamast_model=adamast_model,
            skip_judge=skip_judge,
            judge_call=judge_call,
            refiner_call=refiner_call,
            generation_threshold=threshold,
        )

    try:
        if background_launcher is not None:
            background_launcher()
        else:
            _spawn_worker(workspace.root, Path(store_dir), Path(trace_root))
    except Exception as exc:
        workspace.mark_generation("failed", str(exc))
        return GenerationResult("failed", f"could not start generation: {exc}")
    return GenerationResult(
        "started",
        "generation started in background; MAST remains active until approval",
    )


def run_generation_job(
    workspace: ProgramWorkspace,
    *,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    trace_root: Path | str = DEFAULT_TRACE_ROOT,
    generator: Generator | None = None,
    approver: Approver | None = None,
    adamast_model: str | None = None,
    skip_judge: bool = False,
    judge_call: JudgeCall | None = None,
    refiner_call: Callable[..., Any] | None = None,
    generation_threshold: int = DEFAULT_GENERATION_THRESHOLD,
    activation_poll_seconds: float = 0.05,
    activation_timeout_seconds: float = 86_400,
) -> GenerationResult:
    """Generate, refine via Reflection Judge, then transactionally register.

    When ``skip_judge=True`` refinement is bypassed and the candidate is
    accepted on structural validity alone.
    """
    model = adamast_model or workspace.load().get("adamast_model")
    if not model:
        workspace.mark_generation("failed", "adamast_model is required")
        return GenerationResult("failed", "adamast_model is required")
    try:
        return _generate_and_refine_once(
            workspace,
            store_dir=Path(store_dir),
            trace_root=Path(trace_root),
            model=str(model),
            generator=generator,
            approver=approver,
            skip_judge=skip_judge,
            judge_call=judge_call,
            refiner_call=refiner_call,
            generation_threshold=generation_threshold,
            activation_poll_seconds=activation_poll_seconds,
            activation_timeout_seconds=activation_timeout_seconds,
        )
    except Exception as exc:
        workspace.mark_generation("failed", str(exc))
        return GenerationResult(
            "failed",
            f"generation failed; MAST remains active and traces were preserved: {exc}",
        )
    finally:
        try:
            from adamast.dashboard.server import stop_dashboard_if_idle

            stop_dashboard_if_idle(workspace)
        except Exception:
            pass


def _generate_and_refine_once(
    workspace: ProgramWorkspace,
    *,
    store_dir: Path,
    trace_root: Path,
    model: str,
    generator: Generator | None,
    approver: Approver | None,
    skip_judge: bool,
    judge_call: JudgeCall | None,
    refiner_call: Callable[..., Any] | None,
    generation_threshold: int,
    activation_poll_seconds: float,
    activation_timeout_seconds: float,
) -> GenerationResult:
    """Generate a candidate, refine via the Reflection Judge, then commit.

    Replaces the legacy ``_generate_and_check_once`` (Selection Judge
    accept/reject gate). The Reflection Judge + refiner always produce a
    candidate — there is no rejection path — so the retry-on-rejection
    machinery the old gate needed is gone. ``approver`` is still honored as
    a final escape hatch (e.g. a human-in-the-loop callback).
    """
    traces = [trace.to_dict() for trace in workspace.pending.iter_traces()]
    if not traces:
        raise ValueError("no pending traces available for generation")
    raw = (
        generator(traces)
        if generator is not None
        else _adamast_generate(traces, model, _generation_output_dir(workspace))
    )
    workspace.record_usage_event(
        stage="taxonomy_generation",
        model=model,
        usage_available=False,
        details={
            "trace_count": len(traces),
            "custom_generator": generator is not None,
            "note": "transport did not expose token or cost metadata",
        },
    )
    candidate = candidate_from_adamast(raw, repo=workspace.repo)

    if not skip_judge:
        summary = refine_with_reflection_judge(
            candidate,
            traces,
            adamast_model=model,
            judge_call=judge_call,
            refiner_call=refiner_call,
        )
        workspace.record_usage_event(
            stage="generation_reflection_judge",
            model=model,
            usage_available=False,
            details={
                "trace_count": len(traces),
                "n_traces_judged": summary.n_traces_judged,
                "custom_judge_call": judge_call is not None,
                "custom_refiner_call": refiner_call is not None,
                "note": "transport did not expose token or cost metadata",
            },
        )
        candidate = summary.candidate
        candidate.setdefault("judge_metadata", {}).update({
            "n_traces_judged": summary.n_traces_judged,
            "retired": summary.retired,
            "added": summary.added,
            "edited": summary.edited,
            "split": summary.split,
            "merged": summary.merged,
            "n_proposed_names_distinct": summary.n_proposed_names_distinct,
            "n_weak_mapping_codes": summary.n_weak_mapping_codes,
            "n_unused_codes_in_sample": summary.n_unused_codes_in_sample,
            "judge_warnings": summary.judge_warnings,
        })

    if not structurally_accept(candidate):
        snapshot_count = workspace.pending.count()
        workspace.mark_generation_rejected(
            snapshot_count,
            generation_threshold,
            "candidate failed structural acceptance",
        )
        return GenerationResult(
            "rejected",
            "generated candidate failed structural acceptance; "
            "pending traces were preserved",
        )

    if approver is not None and not approver(candidate):
        snapshot_count = workspace.pending.count()
        workspace.mark_generation_rejected(
            snapshot_count,
            generation_threshold,
            "candidate rejected by approval callback",
        )
        return GenerationResult(
            "rejected",
            "generated candidate was rejected by approval callback; "
            "pending traces were preserved",
        )

    taxonomy_id = _wait_and_commit(
        workspace,
        candidate,
        store_dir=Path(store_dir),
        trace_root=Path(trace_root),
        poll_seconds=activation_poll_seconds,
        timeout_seconds=activation_timeout_seconds,
    )
    return GenerationResult(
        "activated",
        "generated taxonomy refined and activated"
        if not skip_judge
        else "generated taxonomy activated (judge skipped)",
        taxonomy_id,
    )


def _wait_and_commit(
    workspace: ProgramWorkspace,
    candidate: dict[str, Any],
    *,
    store_dir: Path,
    trace_root: Path,
    poll_seconds: float,
    timeout_seconds: float,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while True:
        taxonomy_id = None
        workspace.reconcile_stale_sessions()
        with workspace.locked_manifest() as manifest:
            if not manifest.get("active_sessions"):
                taxonomy_id = _commit_accepted_candidate(
                    workspace,
                    manifest,
                    candidate,
                    store_dir=store_dir,
                    trace_root=trace_root,
                )
        if taxonomy_id:
            try:
                workspace.pending.integrate_into(
                    TraceStore(trace_root / taxonomy_id)
                )
            except OSError:
                # Activation is already durable. Pending duplicates are safer
                # than deleting unverified source data and are cleaned on a
                # later integration attempt.
                pass
            return taxonomy_id
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting for running tasks to finish")
        time.sleep(poll_seconds)


def _commit_accepted_candidate(
    workspace: ProgramWorkspace,
    manifest: dict[str, Any],
    candidate: dict[str, Any],
    *,
    store_dir: Path,
    trace_root: Path,
) -> str:
    taxonomy_id = _new_taxonomy_id(candidate)
    record = {"taxonomy_id": taxonomy_id, **candidate}
    staging = trace_root / f".staging-{taxonomy_id}-{uuid.uuid4().hex}"
    final_traces = trace_root / taxonomy_id
    registered = False
    final_created = False
    try:
        _copy_and_verify(workspace.pending.trace_files(), staging)
        trace_root.mkdir(parents=True, exist_ok=True)
        if final_traces.exists():
            raise FileExistsError(f"taxonomy trace folder already exists: {final_traces}")
        os.replace(staging, final_traces)
        final_created = True
        store.register(record, store_dir)
        registered = True
        manifest["taxonomy_id"] = taxonomy_id
        manifest["generation"] = {"state": "complete", "last_error": None}
    except Exception:
        if registered:
            store.unregister(taxonomy_id, store_dir)
        if final_created:
            shutil.rmtree(final_traces, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return taxonomy_id


def _copy_and_verify(sources: Iterable[Path], staging: Path) -> None:
    staging.mkdir(parents=True, exist_ok=False)
    for source in sources:
        payload = source.read_bytes()
        target = staging / source.name
        target.write_bytes(payload)
        if target.read_bytes() != payload:
            raise OSError(f"staged trace verification failed for {source}")


def _new_taxonomy_id(candidate: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(
        json.dumps(candidate, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]
    return f"tax-{stamp}-{digest}-{uuid.uuid4().hex[:6]}"


def _spawn_worker(trace_output: Path, store_dir: Path, trace_root: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "adamast.learning.generation",
        "--worker",
        "--trace-output",
        str(trace_output),
        "--store-dir",
        str(store_dir),
        "--trace-root",
        str(trace_root),
    ]
    worker_log = Path(trace_output) / "generation_worker.log"
    worker_log.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(worker_log, "a", buffering=1, encoding="utf-8", errors="replace")
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
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
        Path(trace_output) / GENERATION_WORKER_STATE,
        "generation",
        pid=process.pid,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--trace-output", required=True)
    parser.add_argument("--store-dir", required=True)
    parser.add_argument("--trace-root", required=True)
    args = parser.parse_args()
    if not args.worker:
        parser.error("--worker is required")
    workspace = ProgramWorkspace(args.trace_output)
    with WorkerHeartbeat(workspace.root / GENERATION_WORKER_STATE, "generation"):
        result = run_generation_job(
            workspace,
            store_dir=args.store_dir,
            trace_root=args.trace_root,
            adamast_model=workspace.load().get("adamast_model"),
        )
    return 0 if result.action == "activated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
