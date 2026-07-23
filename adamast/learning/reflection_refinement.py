"""End-of-generation validation via the Reflection Judge + LLM refiner.

This module replaces the legacy ``taxonomy_check`` Selection Judge that
previously gated every newly-generated taxonomy. The new flow:

  1. Run the AdaMASTReflectionJudge against a sample of the same traces the
     taxonomy was induced from. The judge identifies failure points, builds
     a causal graph, then maps each failure point to taxonomy codes —
     surfacing both ``unmapped`` (genuinely new failures) and
     ``weak_taxonomy_matches`` (existing codes stretched to fit).
  2. Aggregate the per-trace judge outputs into three signal streams:
       - proposed new codes (keyed by lowercased name + support count);
       - weak-mapped existing codes (per-code with rationales);
       - per-code utilization stats (times_mapped, avg/max confidence,
         frequently-co-mapped codes — drives the RETIRE signal).
  3. Hand the full payload to a refiner LLM with a curated prompt that
     instructs it to ADD genuinely-uncovered codes, EDIT weakly-defined
     ones, SPLIT codes that cluster into multiple distinct patterns, and
     MERGE near-duplicates into one more general code. Outright deletion
     (RETIRE) is disallowed by default (``allow_retire=False``): a code
     unused by trace scanning may still matter to artifact-level detectors.
  4. Apply the refiner's mutations to the Taxonomy in order MERGE → RETIRE
     (if allowed) → EDIT → SPLIT → ADD. Code ids are stable identities:
     surviving codes keep their ids, new codes mint fresh ids above the
     high-water mark, and retired ids are never reused — so evidence and
     checkpoints recorded against the pre-refinement taxonomy keep joining.

The refinement always produces a candidate — there's no rejection. Callers
that want "validation" in the old accept/reject sense get it implicitly:
the refiner removes bloat and adds coverage as needed, so the post-refine
candidate is always at least as good as the pre-refine one. The output
candidate carries a ``judge_metadata`` block with the action counts and
trace coverage so the dashboard can surface what changed.

This implementation operates on adamast's flat ``{repo, domain, codes}``
candidate shape in memory (no file IO) and uses ``learning_calls.refine_json``
for the refiner LLM, with provider routing driven by the configured model id
and environment credentials.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from importlib import resources
from string import Template
from typing import Any, Callable, Iterable, Mapping, Sequence

from adamast import ProjectFn
from adamast.llm.learning_calls import outcome_blind_trace, refine_json
from adamast.core.taxonomy_data import Code, CostMeter, Taxonomy

JudgeCallable = Callable[..., dict]
RefinerCallable = Callable[..., dict | None]

DEFAULT_N_TRACES = 30
DEFAULT_WEAK_MAPPING_THRESHOLD = 0.65
DEFAULT_MAX_WORKERS = 4


def _text_asset(name: str) -> str:
    return (
        resources.files("adamast.learning")
        .joinpath("assets").joinpath(name)
        .read_text(encoding="utf-8")
    )


@dataclass(frozen=True)
class RefinementSummary:
    """What the refiner did, returned alongside the refined candidate."""

    candidate: dict[str, Any]
    n_traces_judged: int
    n_proposed_names_distinct: int
    n_weak_mapping_codes: int
    n_unused_codes_in_sample: int
    retired: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    edited: list[str] = field(default_factory=list)
    split: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    judge_warnings: list[str] = field(default_factory=list)

    @property
    def applied_any(self) -> bool:
        return bool(
            self.retired or self.added or self.edited or self.split or self.merged
        )


# ──────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────


def refine_with_reflection_judge(
    candidate: Mapping[str, Any],
    traces: Sequence[Mapping[str, Any]],
    *,
    adamast_model: str,
    n_traces: int = DEFAULT_N_TRACES,
    judge_mode: str = "two_call",
    weak_mapping_threshold: float = DEFAULT_WEAK_MAPPING_THRESHOLD,
    max_workers: int = DEFAULT_MAX_WORKERS,
    seed: int = 0,
    judge_call: JudgeCallable | None = None,
    refiner_call: RefinerCallable | None = None,
    project_fn: ProjectFn | None = None,
    allow_retire: bool = False,
) -> RefinementSummary:
    """Validate + refine ``candidate`` against ``traces`` via the Reflection Judge.

    Parameters
    ----------
    candidate
        AdaMAST flat candidate dict: ``{repo, domain, codes: [...]}`` as
        produced by ``generation.candidate_from_adamast``.
    traces
        The trace dicts the candidate was induced from. Sampled to
        ``n_traces`` for judging.
    adamast_model
        Model id used both for the AdaMASTReflectionJudge and the refiner LLM.
    judge_call, refiner_call
        Optional injection points for tests. ``judge_call`` matches the
        AdaMASTReflectionJudge LLMCall signature; ``refiner_call`` is the
        thin transport used by ``learning_calls.refine_json``
        (``(prompt, model) -> raw_text | None``).
    project_fn
        Optional per-trace projection (oracle-blind). Defaults to
        ``outcome_blind_trace``. Must take a trace dict and return a dict —
        see ``adamast.ProjectFn``. String-shape callables are
        explicitly disallowed; rewrite ``raw_trajectory`` inside the dict
        and return the dict.

    Returns
    -------
    RefinementSummary with the refined candidate dict plus action counts.
    """
    # Local import keeps reflection_judge dependency lazy.
    from adamast.judges.reflection_judge import AdaMASTReflectionJudge

    if not isinstance(candidate, Mapping) or "codes" not in candidate:
        raise ValueError("candidate must be a flat dict with a 'codes' list")
    if adamast_model is None or not str(adamast_model).strip():
        raise ValueError("adamast_model is required")

    rng = random.Random(seed)
    project = project_fn or outcome_blind_trace

    def _project_safe(trace: Mapping[str, Any]) -> Mapping[str, Any]:
        out = project(trace)
        if not isinstance(out, Mapping):
            raise TypeError(
                f"project_fn returned {type(out).__name__}; must return a "
                f"dict (see adamast.ProjectFn). String-shape callables "
                f"are explicitly disallowed."
            )
        return out

    taxonomy = Taxonomy.from_flat(candidate)
    if not taxonomy.codes:
        # Nothing to refine; return the candidate verbatim.
        return RefinementSummary(
            candidate=_taxonomy_to_flat(taxonomy, candidate),
            n_traces_judged=0,
            n_proposed_names_distinct=0,
            n_weak_mapping_codes=0,
            n_unused_codes_in_sample=0,
        )

    pool = [_judge_input(_project_safe(t)) for t in traces if t]
    pool = [ji for ji in pool if ji.get("trace")]
    if not pool:
        return RefinementSummary(
            candidate=_taxonomy_to_flat(taxonomy, candidate),
            n_traces_judged=0,
            n_proposed_names_distinct=0,
            n_weak_mapping_codes=0,
            n_unused_codes_in_sample=len(taxonomy.codes),
        )

    sample = pool if len(pool) <= n_traces else rng.sample(pool, n_traces)

    meter = CostMeter()
    judge = AdaMASTReflectionJudge(
        taxonomy,
        judge_model=adamast_model,
        meter=meter,
        mode=judge_mode,
        weak_mapping_threshold=weak_mapping_threshold,
        llm_call=judge_call,
    )

    results, judge_warnings = _run_judge_parallel(judge, sample, max_workers)

    proposed = _aggregate_proposed(results)
    weak = _aggregate_weak(results)
    utilization = _build_utilization(taxonomy.codes, results)

    n_unused = sum(1 for u in utilization if u["times_mapped"] == 0)

    if not proposed and not weak and not n_unused:
        # Judge surfaced no actionable signal; refinement is a no-op.
        return RefinementSummary(
            candidate=_taxonomy_to_flat(taxonomy, candidate),
            n_traces_judged=len(sample),
            n_proposed_names_distinct=0,
            n_weak_mapping_codes=0,
            n_unused_codes_in_sample=0,
            judge_warnings=judge_warnings,
        )

    proposals = _call_refiner(
        taxonomy=taxonomy,
        proposed=proposed,
        weak=weak,
        utilization=utilization,
        model=adamast_model,
        refiner_call=refiner_call,
    )

    applied = _apply_proposals(taxonomy, proposals or {}, allow_retire=allow_retire)

    refined_candidate = _taxonomy_to_flat(taxonomy, candidate)
    return RefinementSummary(
        candidate=refined_candidate,
        n_traces_judged=len(sample),
        n_proposed_names_distinct=len(proposed),
        n_weak_mapping_codes=len(weak),
        n_unused_codes_in_sample=n_unused,
        retired=applied["retire"],
        added=applied["add"],
        edited=applied["edit"],
        split=applied["split"],
        merged=applied["merge"],
        judge_warnings=judge_warnings,
    )


# ──────────────────────────────────────────────────────────────────────────
# Stage 1: build judge inputs from adamast trace dicts
# ──────────────────────────────────────────────────────────────────────────


def _judge_input(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one adamast trace dict into the AdaMASTReflectionJudge input shape."""
    return {
        "candidate_id": "generation",
        "task_id": str(trace.get("problem_id") or ""),
        "run_id": str(trace.get("problem_id") or ""),
        "task_prompt": str(trace.get("task") or ""),
        "candidate_output": "(see trace)",
        "trace": str(trace.get("raw_trajectory") or ""),
    }


# ──────────────────────────────────────────────────────────────────────────
# Stage 2: parallel judging
# ──────────────────────────────────────────────────────────────────────────


def _run_judge_parallel(judge, sample, max_workers: int) -> tuple[list[dict], list[str]]:
    """Run ``judge.analyze`` over the sample in parallel; collect results + warnings."""
    results: list[dict] = []
    warnings: list[str] = []
    if not sample:
        return results, warnings

    def _one(ji):
        try:
            return judge.analyze(ji)
        except Exception as exc:
            return {"_error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = [pool.submit(_one, ji) for ji in sample]
        for f in as_completed(futures):
            res = f.result()
            if isinstance(res, dict) and "_error" in res:
                warnings.append(res["_error"])
                continue
            results.append(res)
            md = res.get("judge_metadata") or {}
            for w in (md.get("warnings") or []):
                warnings.append(w)
    return results, warnings


# ──────────────────────────────────────────────────────────────────────────
# Stage 3: signal aggregation
# ──────────────────────────────────────────────────────────────────────────


def _aggregate_proposed(results: Iterable[Mapping[str, Any]]) -> dict[str, dict]:
    """Group unmapped failure points by lowercased proposed name."""
    proposed: dict[str, dict] = {}
    for r in results:
        for up in (r.get("selection_summary", {}).get("unmapped_failure_points") or []):
            name = (up.get("proposed_name") or "").strip()
            key = name.lower()
            if not key:
                continue
            slot = proposed.setdefault(key, {
                "name": name,
                "definitions": [],
                "ruled_out_against": Counter(),
                "support": 0,
            })
            slot["support"] += 1
            if up.get("proposed_definition"):
                slot["definitions"].append(up["proposed_definition"])
            for c in up.get("ruled_out_codes") or []:
                slot["ruled_out_against"][c] += 1
    return proposed


def _aggregate_weak(results: Iterable[Mapping[str, Any]]) -> dict[str, list[dict]]:
    """Group weak-mapping signals by stretched code."""
    weak: dict[str, list[dict]] = {}
    for r in results:
        for wm in (r.get("selection_summary", {}).get("weak_taxonomy_matches") or []):
            code = wm.get("code")
            if code:
                weak.setdefault(code, []).append(wm)
    return weak


def _build_utilization(codes: Sequence[Code], results: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Per-code stats: times_mapped, avg/max confidence, frequently-co-mapped."""
    usage: dict[str, dict[str, Any]] = {
        c.code: {"times_mapped": 0, "confidences": [], "co_mapped_with": Counter()}
        for c in codes
    }
    for r in results:
        for fp in r.get("failure_points") or []:
            mappings = fp.get("taxonomy_mappings") or []
            codes_on_fp = [m.get("code") for m in mappings if m.get("code")]
            confs_on_fp = [m.get("mapping_confidence") for m in mappings]
            for i, code in enumerate(codes_on_fp):
                if code not in usage:
                    continue
                usage[code]["times_mapped"] += 1
                c = confs_on_fp[i]
                if isinstance(c, (int, float)):
                    usage[code]["confidences"].append(float(c))
                for other in codes_on_fp:
                    if other != code and other in usage:
                        usage[code]["co_mapped_with"][other] += 1

    out: list[dict] = []
    for c in codes:
        u = usage.get(c.code, {})
        confs = u.get("confidences") or []
        co = u.get("co_mapped_with") or Counter()
        n = u.get("times_mapped", 0)
        strong_co = {k: v for k, v in co.items() if n > 0 and v / max(n, 1) >= 0.5}
        out.append({
            "code": c.code, "name": c.name,
            "times_mapped": n,
            "avg_confidence": round(sum(confs) / len(confs), 3) if confs else None,
            "max_confidence": round(max(confs), 3) if confs else None,
            "frequently_co_mapped_with": strong_co,
        })
    return out


# ──────────────────────────────────────────────────────────────────────────
# Stage 4: refiner LLM call
# ──────────────────────────────────────────────────────────────────────────


REFINER_SYSTEM = _text_asset("reflection_refiner_system.md")


def _refiner_user_prompt(
    *,
    catalog: str,
    proposed: dict[str, dict],
    weak: dict[str, list[dict]],
    utilization: list[dict],
) -> str:
    add_signals = [
        {
            "proposed_name": c["name"],
            "support_count": c["support"],
            "sample_definitions": c["definitions"][:3],
            "ruled_out_against": dict(c["ruled_out_against"]),
        }
        for c in proposed.values()
    ]
    edit_signals = [
        {
            "existing_code": code,
            "weak_mapping_count": len(wms),
            "sample_rationales": [
                (wm.get("mapping_rationale") or "")[:200] for wm in wms[:5]
            ],
        }
        for code, wms in weak.items()
    ]
    return Template(_text_asset("reflection_refiner_user.md")).substitute(
        catalog=catalog,
        add_signals=json.dumps(add_signals, indent=2),
        edit_signals=json.dumps(edit_signals, indent=2),
        utilization=json.dumps(utilization, indent=2),
    )


def _call_refiner(
    *,
    taxonomy: Taxonomy,
    proposed: dict[str, dict],
    weak: dict[str, list[dict]],
    utilization: list[dict],
    model: str,
    refiner_call: RefinerCallable | None,
) -> dict | None:
    """Build the refiner prompt and invoke the model. Returns parsed JSON or None."""
    user = _refiner_user_prompt(
        catalog=taxonomy.prompt_block(),
        proposed=proposed,
        weak=weak,
        utilization=utilization,
    )
    # learning_calls.refine_json takes a SINGLE combined prompt; concatenate
    # system + user the way the reflection-judge bridge does.
    combined = f"{REFINER_SYSTEM}\n\n{user}"
    return refine_json(combined, model=model, call=refiner_call)


# ──────────────────────────────────────────────────────────────────────────
# Stage 5: apply mutations
# ──────────────────────────────────────────────────────────────────────────


def _apply_proposals(
    taxonomy: Taxonomy,
    proposals: Mapping[str, Any],
    *,
    allow_retire: bool = False,
) -> dict[str, list[str]]:
    """Apply refiner output in order MERGE → RETIRE → EDIT → SPLIT → ADD.

    Merge consolidates >=2 codes into one more general code (the sources are
    removed only after the combined code is inserted — no pattern is lost).
    The merged code is a new concept, so it mints a fresh id; the sources'
    retired ids keep their recorded evidence and are never reused.
    Outright RETIRE proposals are ignored unless ``allow_retire=True``: the
    default policy is consolidate-never-discard, since a code unused by one
    detector (trace scanning) may still matter to another (artifact checking).
    Surviving codes never change ids; lookups resolve through a pre-phase
    snapshot's stable uids.
    """
    applied: dict[str, list[str]] = {
        "merge": [], "retire": [], "edit": [], "split": [], "add": [],
    }

    idx = taxonomy.code_index()
    for mg in (proposals.get("merge") or []):
        if not isinstance(mg, Mapping) or not mg.get("name"):
            continue
        sources = [idx.get(code) for code in (mg.get("codes") or [])]
        sources = [c for c in sources if c is not None and taxonomy.by_uid(c.uid)]
        if len(sources) < 2:
            continue
        cat = str(mg.get("category") or sources[0].category).upper()[:1]
        cat = cat if cat in ("A", "B", "C") else sources[0].category
        uid = taxonomy.add(cat, dict(mg))
        for src in sources:
            taxonomy.retire(src.uid)
        merged_code = taxonomy.by_uid(uid)
        applied["merge"].append(
            f"{'+'.join(c.name for c in sources)} -> "
            f"{merged_code.code if merged_code else '?'} {mg['name']}"
        )

    idx = taxonomy.code_index()
    for rt in (proposals.get("retire") or []):
        if not isinstance(rt, Mapping):
            continue
        c = idx.get(rt.get("code"))
        if not c or not allow_retire:
            continue
        applied["retire"].append(f"{rt['code']} {c.name}")
        taxonomy.retire(c.uid)

    idx = taxonomy.code_index()
    for ed in (proposals.get("edit") or []):
        if not isinstance(ed, Mapping):
            continue
        c = idx.get(ed.get("code"))
        if not c:
            continue
        taxonomy.edit(c.uid, name=ed.get("name"), definition=ed.get("definition"))
        applied["edit"].append(ed["code"])

    idx = taxonomy.code_index()
    for sp in (proposals.get("split") or []):
        if not isinstance(sp, Mapping):
            continue
        c = idx.get(sp.get("code"))
        into = sp.get("into") or []
        if not c or len(into) < 2:
            continue
        taxonomy.split(c.uid, list(into))
        applied["split"].append(f"{sp['code']} -> {len(into)}")

    for add in (proposals.get("add") or []):
        if not isinstance(add, Mapping) or not add.get("name"):
            continue
        cat = "A" if str(add.get("category", "C")).upper().startswith("A") else "C"
        uid = taxonomy.add(cat, dict(add))
        c = taxonomy.by_uid(uid)
        if c is not None:
            applied["add"].append(f"{c.code} {c.name}")

    taxonomy.version += 1
    return applied


# ──────────────────────────────────────────────────────────────────────────
# Serialization helpers
# ──────────────────────────────────────────────────────────────────────────


def _taxonomy_to_flat(taxonomy: Taxonomy, source: Mapping[str, Any]) -> dict[str, Any]:
    """Render a Taxonomy back to adamast's flat ``{repo, domain, codes}`` shape.

    Carries over ``repo`` / ``domain`` from the source candidate so downstream
    code (registration, lineage) sees the same identity it expected, plus the
    ratcheted ``id_high_water`` floor so ids retired here are never reused by
    a later refinement round.
    """
    out_codes: list[dict[str, Any]] = []
    for c in taxonomy.codes:
        entry: dict[str, Any] = {
            "id": c.code,
            "name": c.name,
            "description": c.definition,
            "category": c.category,
        }
        if c.severity and c.severity != "major":
            entry["severity"] = c.severity
        if c.category == "B" and c.applies_to_role:
            entry["applies_to_role"] = c.applies_to_role
        if c.detection_heuristics:
            entry["detection_heuristics"] = list(c.detection_heuristics)
        out_codes.append(entry)
    flat: dict[str, Any] = {
        "repo": source.get("repo", ""),
        "domain": source.get("domain", ""),
        "codes": out_codes,
    }
    high_water = taxonomy.metadata.get("id_high_water")
    if isinstance(high_water, dict) and high_water:
        flat["id_high_water"] = dict(high_water)
    return flat
