"""General, agent- and model-agnostic AdaMAST program/task lifecycle."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from adamast.core import mast, resolver, store

from adamast.learning.generation import (
    DEFAULT_GENERATION_THRESHOLD,
    Approver,
    GenerationResult,
    Generator,
    trigger_generation,
)
from .lineage import TaxonomyLineage
from .program import ProgramWorkspace
from adamast.protocol.gate import GateDecision, evaluate_pre_submission, render_protocol
from .traces import (
    DEFAULT_TRACE_ROOT,
    GenerationTrace,
    RetentionReport,
    TraceStore,
)
from adamast.learning.refinement import (
    DEFAULT_K,
    DEFAULT_K_INIT,
    Approver as RefinementApprover,
    Refiner,
    RefinementJudge,
    RefinementRepairer,
    RefinementResult,
    trigger_refinement,
)


@dataclass(frozen=True)
class SessionDelivery:
    """Content the calling agent or framework delivers to its model.

    AdaMAST returns the selected taxonomy and runtime protocol; how they reach the
    model (system prompt, tool context, instruction file, etc.) is the caller's
    choice and not AdaMAST's concern.
    """

    taxonomy_id: str
    taxonomy: dict
    runtime_protocol: str
    dashboard_url: str | None = None


@dataclass
class Session:
    session_id: str
    program_id: str
    workspace: ProgramWorkspace
    delivery: SessionDelivery
    store_dir: Path
    trace_root: Path
    max_retries: int
    generation_threshold: int
    generation_stops: bool
    adamast_model: str | None
    skip_judge: bool
    k_init: int
    k: int
    refinement_stops: bool
    advanced_refinement: bool
    freeze: bool
    evidence_export: Path | None
    _pending_traces: list[GenerationTrace] = field(default_factory=list)
    _ended: bool = False


@dataclass(frozen=True)
class SessionEndResult:
    persisted_traces: int
    integrated_traces: int
    retention: RetentionReport
    generation: GenerationResult
    refinement: RefinementResult
    evidence_export_path: Path | None = None
    evidence_export_error: str | None = None


def start_session(
    inherit=resolver.ABSENT,
    *,
    trace_output: Path | str,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    trace_root: Path | str = DEFAULT_TRACE_ROOT,
    launcher=None,
    session_id: str | None = None,
    max_retries: int = 3,
    generation_threshold: int = DEFAULT_GENERATION_THRESHOLD,
    generation_stops: bool = False,
    adamast_model: str | None = None,
    skip_judge: bool = False,
    k_init: int = DEFAULT_K_INIT,
    k: int = DEFAULT_K,
    refinement_stops: bool = False,
    advanced_refinement: bool = False,
    freeze: bool = False,
    evidence_export: Path | str | None = None,
    repo: str | None = None,
    repo_path: Path | str | None = None,
    dashboard: bool = True,
) -> Session:
    """Start one task; trace_output is mandatory and identifies its program."""
    if generation_threshold <= 0:
        raise ValueError("generation_threshold must be positive")
    if k_init <= 0 or k <= 0:
        raise ValueError("K_init and K must be positive")
    workspace = ProgramWorkspace(
        trace_output,
        repo=repo,
        repo_path=repo_path,
    )
    store_dir = Path(store_dir)
    trace_root = Path(trace_root)
    sid = session_id or uuid.uuid4().hex

    requested_id: str | None = None
    if inherit is not resolver.ABSENT:
        decision = resolver.resolve(
            inherit,
            store_dir=store_dir,
            launcher=launcher,
        )
        if decision != resolver.NONE:
            # Conversation branches pin the exact selected version. Unbranched
            # CLI/legacy programs retain their historical single-chain behavior.
            requested_id = (
                decision
                if workspace.branch_id
                else TaxonomyLineage(store_dir).resolve_latest(decision)
            )

    current_id = workspace.load().get("taxonomy_id")
    if current_id and not workspace.branch_id:
        latest = TaxonomyLineage(store_dir).resolve_latest(str(current_id))
        if latest != current_id:
            workspace.follow_taxonomy_successor(latest)

    trace_root = workspace.scoped_trace_root(trace_root)

    try:
        selected_id = workspace.begin_session(sid, requested_id, adamast_model)
        taxonomy = (
            mast.MAST
            if selected_id == mast.MAST_ID
            else store.fetch_by_id(selected_id, store_dir)
        )
    except Exception:
        workspace.finish_session(sid)
        raise

    dashboard_url = None
    if dashboard:
        try:
            from adamast.dashboard.server import ensure_dashboard

            dashboard_url = ensure_dashboard(workspace, store_dir)
        except Exception:
            dashboard_url = None
    delivery = SessionDelivery(
        taxonomy_id=selected_id,
        taxonomy=taxonomy,
        runtime_protocol=render_protocol(max_retries),
        dashboard_url=dashboard_url,
    )
    return Session(
        session_id=sid,
        program_id=workspace.program_id,
        workspace=workspace,
        delivery=delivery,
        store_dir=store_dir,
        trace_root=trace_root,
        max_retries=max_retries,
        generation_threshold=generation_threshold,
        generation_stops=generation_stops,
        adamast_model=adamast_model or workspace.load().get("adamast_model"),
        skip_judge=skip_judge,
        k_init=k_init,
        k=k,
        refinement_stops=refinement_stops,
        advanced_refinement=advanced_refinement,
        freeze=freeze,
        evidence_export=Path(evidence_export) if evidence_export else None,
    )


def pre_submission(
    session: Session,
    gate_text: str,
    *,
    repair_attempts_used: int = 0,
) -> GateDecision:
    _require_active(session)
    return evaluate_pre_submission(
        gate_text,
        max_retries=session.max_retries,
        repair_attempts_used=repair_attempts_used,
    )


def record_trace(session: Session, trace: GenerationTrace) -> int:
    _require_active(session)
    session._pending_traces.append(trace)
    return len(session._pending_traces)


def end_session(
    session: Session,
    *,
    generator: Generator | None = None,
    approver: Approver | None = None,
    background_launcher: Callable[[], None] | None = None,
    judge_call=None,
    refiner: Refiner | None = None,
    refinement_approver: RefinementApprover | None = None,
    refinement_judge: RefinementJudge | None = None,
    refinement_repairer: RefinementRepairer | None = None,
    refinement_background_launcher: Callable[[], None] | None = None,
    pre_persisted_trace_names: list[str] | tuple[str, ...] | None = None,
) -> SessionEndResult:
    """Finish the task, persist traces, and run the applicable transition."""
    _require_active(session)
    trace_names = list(pre_persisted_trace_names or ())
    trace_names.extend(
        session.workspace.pending.append_many_with_names(session._pending_traces)
    )
    persisted = len(trace_names)
    session._pending_traces.clear()
    session.workspace.finish_session(session.session_id)
    session._ended = True

    integrated = 0
    generation = GenerationResult("none", "taxonomy generation not applicable")
    refinement = RefinementResult("none", "taxonomy refinement not applicable")
    if session.freeze:
        if session.delivery.taxonomy_id != mast.MAST_ID:
            destination = TraceStore(
                session.trace_root / session.delivery.taxonomy_id
            )
            integrated = session.workspace.pending.integrate_into(destination)
            refinement = RefinementResult(
                "frozen",
                "freeze mode enabled; refinement skipped",
            )
        else:
            generation = GenerationResult(
                "frozen",
                "freeze mode enabled; generation skipped",
            )
    elif session.delivery.taxonomy_id != mast.MAST_ID:
        destination = TraceStore(
            session.trace_root / session.delivery.taxonomy_id
        )
        integrated = session.workspace.pending.integrate_into(destination)
        session.workspace.add_refinement_traces(
            session.delivery.taxonomy_id,
            trace_names,
        )
        refinement = trigger_refinement(
            session.workspace,
            store_dir=session.store_dir,
            trace_root=session.trace_root,
            k_init=session.k_init,
            k=session.k,
            refinement_stops=session.refinement_stops,
            advanced_refinement=session.advanced_refinement,
            adamast_model=session.adamast_model,
            refiner=refiner,
            approver=refinement_approver,
            judge=refinement_judge,
            repairer=refinement_repairer,
            background_launcher=refinement_background_launcher,
        )
    else:
        generation = trigger_generation(
            session.workspace,
            store_dir=session.store_dir,
            trace_root=session.trace_root,
            threshold=session.generation_threshold,
            generation_stops=session.generation_stops,
            generator=generator,
            approver=approver,
            adamast_model=session.adamast_model,
            skip_judge=session.skip_judge,
            judge_call=judge_call,
            background_launcher=background_launcher,
        )

    active_id = session.workspace.load().get("taxonomy_id")
    retained = (
        TraceStore(session.trace_root / active_id)
        if active_id
        else session.workspace.pending
    )
    try:
        from adamast.dashboard.server import stop_dashboard_if_idle

        stop_dashboard_if_idle(session.workspace)
    except Exception:
        pass
    evidence_export_path = None
    evidence_export_error = None
    if session.evidence_export:
        try:
            from .evidence_export import export_program_evidence

            evidence_export_path = export_program_evidence(
                session.workspace,
                session.evidence_export,
            )
        except Exception as exc:  # noqa: BLE001
            evidence_export_error = str(exc)
    return SessionEndResult(
        persisted_traces=persisted,
        integrated_traces=integrated,
        retention=retained.retention_report(),
        generation=generation,
        refinement=refinement,
        evidence_export_path=evidence_export_path,
        evidence_export_error=evidence_export_error,
    )


def _require_active(session: Session) -> None:
    if session._ended:
        raise RuntimeError(f"AdaMAST session {session.session_id!r} has already ended")
