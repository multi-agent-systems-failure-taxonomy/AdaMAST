"""AdaMAST: adaptive failure-mode taxonomies for agent harnesses.

Single package layout:

- ``adamast.core``      taxonomy/evidence/trace/reflection data model and session lifecycle
- ``adamast.protocol``  the compact-checkpoint protocol and pre-submission gate
- ``adamast.judges``    judge implementations over recorded evidence
- ``adamast.llm``       provider/transport layer for model calls
- ``adamast.learning``  taxonomy generation and refinement
- ``adamast.hosts``     host integrations (Claude Code, Codex, single-LLM, interactive)
- ``adamast.dashboard`` local dashboard, status, and web views
- ``adamast.cli``       the umbrella ``adamast`` command
"""

from __future__ import annotations

from typing import Any, Callable, Mapping


# ── Shared callable type contracts ──────────────────────────────────────
#
# Every entry point that exposes a ``project_fn`` (oracle-blind trace
# projection) MUST use this type, so callers can write a single function
# usable across generation, refinement, and registration without hitting
# silent shape mismatches between modules. The convention: a project_fn
# takes a trace dict (the canonical AdaMAST trace record) and returns a
# dict (possibly the same one, possibly a rewritten copy). String-only
# variants are explicitly disallowed; if you need to rewrite only the
# raw_trajectory text, do it inside the dict and return the mutated dict.
ProjectFn = Callable[[Mapping[str, Any]], Mapping[str, Any]]


from .core.lifecycle import (
    Session,
    SessionDelivery,
    SessionEndResult,
    end_session,
    pre_submission,
    record_trace,
    start_session,
)
from .core.config import load_adamast_config
from .protocol.gate import (
    GateDecision,
    evaluate_pre_submission,
    pin_gate_decision,
    render_protocol,
)
from .learning.generation import GenerationResult
from .core.program import ProgramConflict, ProgramWorkspace
from .core.options import RuntimeOptions, add_runtime_arguments, parse_runtime_args
from .learning.refinement import RefinementResult
from .protocol.checkpoint_prompt import render_format_repair, render_reflection_prompt
from .core.evidence import EVIDENCE_FILE, record_reflection
from .core.reflection import (
    CodeAssignment,
    HarvestedReflection,
    PartialReflection,
    ReflectionResult,
    harvest_reflection,
    parse_reflection,
)
from .dashboard.server import (
    build_server as build_dashboard_server,
    current_taxonomy,
    ensure_dashboard,
    stop_dashboard,
    stop_dashboard_if_idle,
)
from .core.repository import discover_repo
from .core.project_scope import canonical_project_root, project_key, project_program_path
from .core.redaction import redact_text, redact_trace
from .core.traces import (
    GenerationTrace,
    RetentionPolicy,
    RetentionReport,
    TraceStore,
)
from .dashboard.status import program_health

__all__ = [
    "GateDecision",
    "GenerationResult",
    "GenerationTrace",
    "CodeAssignment",
    "EVIDENCE_FILE",
    "HarvestedReflection",
    "PartialReflection",
    "ProgramConflict",
    "ProgramWorkspace",
    "ProjectFn",
    "ReflectionResult",
    "RuntimeOptions",
    "RetentionPolicy",
    "RetentionReport",
    "RefinementResult",
    "build_dashboard_server",
    "current_taxonomy",
    "discover_repo",
    "canonical_project_root",
    "project_key",
    "project_program_path",
    "ensure_dashboard",
    "Session",
    "SessionDelivery",
    "SessionEndResult",
    "TraceStore",
    "end_session",
    "evaluate_pre_submission",
    "harvest_reflection",
    "pin_gate_decision",
    "pre_submission",
    "record_trace",
    "record_reflection",
    "render_format_repair",
    "render_protocol",
    "render_reflection_prompt",
    "load_adamast_config",
    "parse_reflection",
    "program_health",
    "redact_text",
    "redact_trace",
    "start_session",
    "stop_dashboard",
    "stop_dashboard_if_idle",
    "add_runtime_arguments",
    "parse_runtime_args",
]


# ── Public taxonomy-from-traces API (adopted from the public mirror) ────
#
# ``from adamast import generate_taxonomy, judge_trace`` keeps working as
# documented for the published package. These resolve lazily (PEP 562) and
# stay out of ``__all__`` so legacy ``from adamast import *`` consumers do
# not pay for the generation pipeline import.
_PUBLIC_API = {
    "generate_taxonomy": ("adamast.learning.api", "generate_taxonomy"),
    "build_public_taxonomy": ("adamast.learning.api", "build_public_taxonomy"),
    "prepare_taxonomy_for_agreement": (
        "adamast.learning.api",
        "prepare_taxonomy_for_agreement",
    ),
    "Diagnosis": ("adamast.judges.contract", "Diagnosis"),
    "JudgeResponseError": ("adamast.judges.contract", "JudgeResponseError"),
    "TaxonomyJudge": ("adamast.judges.contract", "TaxonomyJudge"),
    "create_judge": ("adamast.judges.contract", "create_judge"),
    "judge_trace": ("adamast.judges.contract", "judge_trace"),
    "judge_traces": ("adamast.judges.contract", "judge_traces"),
    "load_trace_bundle": ("adamast.core.trace_formats", "load_trace_bundle"),
    "load_traces": ("adamast.core.trace_formats", "load_traces"),
    "write_normalized_jsonl": (
        "adamast.core.trace_formats",
        "write_normalized_jsonl",
    ),
    "render_taxonomy_html": ("adamast.dashboard.viewer", "render_taxonomy_html"),
}


def __getattr__(name: str):
    try:
        module_path, attribute = _PUBLIC_API[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    from importlib import import_module

    return getattr(import_module(module_path), attribute)
