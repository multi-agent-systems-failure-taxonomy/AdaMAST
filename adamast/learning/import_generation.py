"""Generate and store an inheritable taxonomy from user-supplied traces.

The upstream AdaMAST loader and eight-stage pipeline own trace normalization and
taxonomy induction. This module adds adamast lifecycle semantics:

* canonical ``GenerationTrace`` validation;
* optional Reflection-Judge refinement of the generated candidate;
* unique taxonomy ID allocation;
* transactional taxonomy + trace registration;
* no program binding or automatic activation.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from adamast.core import store
from adamast.learning.vendor import generate_taxonomy as upstream_generate_taxonomy
from adamast.learning.vendor import load_traces

from adamast.core.config import (
    add_config_argument,
    bool_config_value,
    config_value,
    load_adamast_config,
    require_config_value,
)
from .generation import candidate_from_adamast
from adamast.llm.learning_calls import outcome_blind_trace
from .reflection_refinement import RefinementSummary, refine_with_reflection_judge
from adamast.core.repository import discover_repo
from adamast.core.traces import DEFAULT_TRACE_ROOT, GenerationTrace, TraceStore

Generator = Callable[[list[dict[str, Any]]], dict[str, Any]]


@dataclass(frozen=True)
class ImportedTaxonomyResult:
    taxonomy_id: str
    trace_count: int
    active_codes: tuple[str, ...]
    taxonomy_path: Path
    trace_path: Path
    artifacts_path: Path

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        for field in ("taxonomy_path", "trace_path", "artifacts_path"):
            record[field] = str(record[field])
        record["active_codes"] = list(self.active_codes)
        return record


def generate_imported_taxonomy(
    traces: Path | str | Iterable[Any],
    *,
    adamast_model: str,
    store_dir: Path | str = store.DEFAULT_STORE_DIR,
    trace_root: Path | str = DEFAULT_TRACE_ROOT,
    repo: str | None = None,
    repo_path: Path | str | None = None,
    max_codes: int = 0,
    skip_judge: bool = False,
    save_intermediate: bool = True,
    verbose: bool = True,
    generator: Generator | None = None,
    judge_call: Callable[..., Any] | None = None,
    refiner_call: Callable[..., Any] | None = None,
) -> ImportedTaxonomyResult:
    """Generate and register a dormant taxonomy for later inheritance.

    When ``skip_judge=False`` (default), the generated candidate is refined
    via the Reflection Judge before registration: failure-point analysis +
    mapping + refiner mutations (add / edit / split / retire). The refined
    candidate always succeeds — there is no rejection path.

    When ``skip_judge=True``, the candidate is accepted on structural
    validity alone (no judge call, no refinement).
    """
    if not str(adamast_model).strip():
        raise ValueError("adamast_model is required")
    if max_codes < 0:
        raise ValueError("max_codes cannot be negative")

    canonical = _load_canonical_traces(traces, verbose=verbose)
    store_dir = Path(store_dir).expanduser().resolve()
    trace_root = Path(trace_root).expanduser().resolve()
    display_repo = discover_repo(repo, repo_path)
    staging_parent = store_dir / "_state"
    staging_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".import-staging-",
        dir=staging_parent,
    ) as temporary:
        staging = Path(temporary)
        generation_dir = staging / "generation"
        generation_traces = [
            outcome_blind_trace(trace.to_dict())
            for trace in canonical
        ]
        raw = (
            generator(generation_traces)
            if generator is not None
            else upstream_generate_taxonomy(
                traces=generation_traces,
                output_dir=generation_dir,
                model=adamast_model,
                max_codes=max_codes,
                save_intermediate=save_intermediate,
                verbose=verbose,
            )
        )
        candidate = candidate_from_adamast(raw, repo=display_repo)

        summary: RefinementSummary | None = None
        if not skip_judge:
            summary = refine_with_reflection_judge(
                candidate,
                generation_traces,
                adamast_model=adamast_model,
                judge_call=judge_call,
                refiner_call=refiner_call,
            )
            candidate = summary.candidate

        if not (isinstance(candidate, dict)
                and isinstance(candidate.get("codes"), list)
                and candidate["codes"]):
            raise ValueError(
                "generated taxonomy is structurally invalid (empty or "
                "missing codes); no taxonomy was stored"
            )

        active_codes = tuple(str(code.get("id", "")) for code in candidate["codes"])

        taxonomy_id = _new_taxonomy_id(candidate)
        record = {"taxonomy_id": taxonomy_id, **candidate}
        taxonomy_path, trace_path = _commit(
            record,
            canonical,
            store_dir=store_dir,
            trace_root=trace_root,
        )
        artifacts_path = store_dir / "_state" / "imports" / taxonomy_id
        try:
            _persist_artifacts(
                staging,
                artifacts_path,
                source=traces,
                adamast_model=adamast_model,
                trace_count=len(canonical),
                summary=summary,
                active_codes=active_codes,
            )
        except Exception:
            store.unregister(taxonomy_id, store_dir)
            shutil.rmtree(trace_path, ignore_errors=True)
            raise
        return ImportedTaxonomyResult(
            taxonomy_id=taxonomy_id,
            trace_count=len(canonical),
            active_codes=active_codes,
            taxonomy_path=taxonomy_path,
            trace_path=trace_path,
            artifacts_path=artifacts_path,
        )


def _load_canonical_traces(
    source: Path | str | Iterable[Any],
    *,
    verbose: bool,
) -> list[GenerationTrace]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_file() and path.suffix.lower() not in {".json", ".jsonl"}:
            loaded = load_traces([path.read_text(encoding="utf-8")], verbose=verbose)
        else:
            loaded = load_traces(source, verbose=verbose)
    else:
        loaded = load_traces(source, verbose=verbose)
    canonical: list[GenerationTrace] = []
    for record in loaded:
        try:
            canonical.append(
                GenerationTrace(
                    problem_id=str(record.get("problem_id", "")).strip(),
                    task=str(record.get("task", "")),
                    raw_trajectory=str(record.get("raw_trajectory", "")),
                    metadata=dict(record.get("metadata") or {}),
                )
            )
        except (TypeError, ValueError):
            continue
    if not canonical:
        raise ValueError(
            "no valid traces could be loaded; provide a supported JSON, JSONL, "
            "directory, conversation, Codex, event-log, KIRA, tau-bench, or "
            "canonical AdaMAST trace source"
        )
    return canonical


def _commit(
    record: dict[str, Any],
    traces: list[GenerationTrace],
    *,
    store_dir: Path,
    trace_root: Path,
) -> tuple[Path, Path]:
    taxonomy_id = str(record["taxonomy_id"])
    staging = trace_root / f".staging-{taxonomy_id}-{uuid.uuid4().hex}"
    final_traces = trace_root / taxonomy_id
    taxonomy_path: Path | None = None
    final_created = False
    try:
        TraceStore(staging).append_many(traces)
        trace_root.mkdir(parents=True, exist_ok=True)
        if final_traces.exists():
            raise FileExistsError(
                f"taxonomy trace folder already exists: {final_traces}"
            )
        os.replace(staging, final_traces)
        final_created = True
        taxonomy_path = store.register(record, store_dir)
        return taxonomy_path, final_traces
    except Exception:
        if taxonomy_path is not None:
            store.unregister(taxonomy_id, store_dir)
        if final_created:
            shutil.rmtree(final_traces, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _persist_artifacts(
    staging: Path,
    destination: Path,
    *,
    source: Any,
    adamast_model: str,
    trace_count: int,
    summary: RefinementSummary | None,
    active_codes: tuple[str, ...],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}-{uuid.uuid4().hex}.tmp"
    temporary.mkdir()
    try:
        generation = staging / "generation"
        if generation.exists():
            shutil.copytree(generation, temporary / "generation")
        refinement_block: dict[str, Any] = {"applied": False}
        if summary is not None:
            refinement_block = {
                "applied": True,
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
            }
        (temporary / "import.json").write_text(
            json.dumps(
                {
                    "source": (
                        str(source)
                        if isinstance(source, (str, Path))
                        else "iterable"
                    ),
                    "adamast_model": adamast_model,
                    "trace_count": trace_count,
                    "active_codes": list(active_codes),
                    "refinement": refinement_block,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _new_taxonomy_id(candidate: dict[str, Any]) -> str:
    import hashlib

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(
        json.dumps(candidate, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]
    return f"tax-{stamp}-{digest}-{uuid.uuid4().hex[:6]}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate and store an inheritable AdaMAST taxonomy from user traces."
        )
    )
    add_config_argument(parser)
    parser.add_argument("--traces", required=True)
    parser.add_argument("--adamast-model")
    parser.add_argument("--store-dir")
    parser.add_argument("--trace-root")
    parser.add_argument("--repo")
    parser.add_argument("--repo-path")
    parser.add_argument("--max-codes", type=int, default=0)
    parser.add_argument(
        "--skip-judge",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "skip the Reflection Judge + refiner step at the end of "
            "generation. Generated taxonomies are then accepted on "
            "structural validity alone"
        ),
    )
    parser.add_argument("--no-intermediate", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_adamast_config(args.config)
        result = generate_imported_taxonomy(
            args.traces,
            adamast_model=require_config_value(
                args, config, "adamast_model", "--adamast-model"
            ),
            store_dir=config_value(args, config, "store_dir", store.DEFAULT_STORE_DIR),
            trace_root=config_value(args, config, "trace_root", DEFAULT_TRACE_ROOT),
            repo=config_value(args, config, "repo"),
            repo_path=config_value(args, config, "repo_path"),
            max_codes=args.max_codes,
            skip_judge=bool_config_value(args, config, "skip_judge", False),
            save_intermediate=not args.no_intermediate,
            verbose=not args.quiet,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
