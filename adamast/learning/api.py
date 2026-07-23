"""High-level public API.

One entry point for the common workflow:

- :func:`generate_taxonomy` — give it traces and an output directory and
  it runs draft generation plus the four-annotator agreement gate, writes
  ``taxonomy.json`` / ``manifest.json`` / ``taxonomy.html``, and returns
  the taxonomy dict.

The heavy lifting lives in :mod:`adamast.pipeline` (the ported draft and
agreement engines); this module only wires them together.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from .pipeline.agreement import Config as AgreementConfig
from .pipeline.agreement import TaxonomyRefinerPipeline
from .pipeline.draft import Config as DraftConfig
from .pipeline.draft import LLMNomos
from adamast.llm.providers import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    create_provider,
    normalize_provider_name,
    resolve_model,
    validate_provider_credentials,
)
from adamast.core.trace_formats import load_trace_bundle, write_normalized_jsonl

PIPELINE_NAME = "baseline"
DEFAULT_KAPPA_TARGET = 0.75
DEFAULT_COVERAGE_FLOOR = 0.70
DEFAULT_MAX_ROUNDS = 5


def generate_taxonomy(
    traces: Path | str,
    output: Path | str,
    *,
    provider: str | None,
    model: str | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    kappa_target: float = DEFAULT_KAPPA_TARGET,
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR,
    no_early_stop: bool = False,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    aws_region: str | None = None,
    aws_profile: str | None = None,
    open_viewer: bool = False,
) -> dict[str, Any]:
    """Generate an agreement-gated failure taxonomy from traces.

    Parameters
    ----------
    traces
        Path to a trace file or directory in any accepted format.
    output
        Directory that receives ``taxonomy.json``, ``manifest.json``,
        ``taxonomy.html``, and the ``artifacts/`` tree.
    provider
        Model transport: ``openai``, ``anthropic``, ``google``, or
        ``bedrock``. Prompts are identical across providers.
    model
        Provider model ID. Falls back to the provider's model environment
        variable (for example ``BEDROCK_MODEL_ID``).
    max_rounds, kappa_target, coverage_floor, no_early_stop
        Agreement-gate controls. The result is marked ``accepted`` only
        when macro Fleiss kappa and error coverage both pass.
    max_output_tokens
        Per-call output ceiling for every model request.
    aws_region, aws_profile
        Bedrock-only connection settings.
    open_viewer
        Open the rendered ``taxonomy.html`` in the default browser.

    Returns
    -------
    dict
        The public taxonomy document. ``result["status"]`` is either
        ``"accepted"`` or ``"review_required"``.
    """

    provider = normalize_provider_name(provider)
    validate_provider_credentials(provider)
    model = resolve_model(provider, model)

    traces = Path(traces)
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifacts = output / "artifacts"
    draft_dir = artifacts / "draft"
    agreement_dir = artifacts / "agreement"
    inputs_dir = artifacts / "inputs"
    for directory in (draft_dir, agreement_dir, inputs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    bundle = load_trace_bundle(traces)
    normalized_path = write_normalized_jsonl(
        bundle.traces, inputs_dir / "traces.normalized.jsonl"
    )
    trace_report_path = _write_json(
        inputs_dir / "trace_report.json", bundle.report()
    )

    DraftConfig.PROVIDER = provider
    DraftConfig.MODEL = model
    draft_provider = create_provider(
        provider,
        model,
        timeout=DraftConfig.TIMEOUT,
        max_output_tokens=max_output_tokens,
        aws_region=aws_region,
        aws_profile=aws_profile,
    )
    draft = LLMNomos(normalized_path, draft_dir, client=draft_provider).run()
    if not isinstance(draft, dict):
        raise RuntimeError("draft generation did not return a taxonomy")

    draft_path = _write_json(output / "taxonomy.draft.json", draft)
    agreement_input = prepare_taxonomy_for_agreement(draft)
    agreement_input_path = _write_json(
        agreement_dir / "taxonomy.input.json", agreement_input
    )

    AgreementConfig.PROVIDER = provider
    AgreementConfig.MODEL = model
    AgreementConfig.MAX_ROUNDS = max_rounds
    AgreementConfig.KAPPA_TARGET = kappa_target
    AgreementConfig.COVERAGE_FLOOR = coverage_floor
    AgreementConfig.NO_EARLY_STOP = no_early_stop

    agreement_provider = create_provider(
        provider,
        model,
        timeout=120,
        max_output_tokens=max_output_tokens,
        aws_region=aws_region,
        aws_profile=aws_profile,
    )
    agreement = TaxonomyRefinerPipeline(
        agreement_input_path,
        normalized_path,
        agreement_dir,
        client=agreement_provider,
    )
    agreement_summary = agreement.run()
    if not isinstance(agreement_summary, dict):
        raise RuntimeError("agreement process did not return a result")

    final_kappa = float(agreement_summary.get("final_kappa") or 0.0)
    final_coverage = float(agreement_summary.get("final_coverage") or 0.0)
    accepted = final_kappa >= kappa_target and final_coverage >= coverage_floor
    status = "accepted" if accepted else "review_required"

    taxonomy = build_public_taxonomy(
        agreement.taxonomy,
        draft=draft,
        status=status,
        provider=provider,
        model=model,
        trace_count=len(bundle.traces),
        agreement_summary=agreement_summary,
    )
    taxonomy_path = _write_json(output / "taxonomy.json", taxonomy)

    manifest = {
        "schema_version": 1,
        "strategy": PIPELINE_NAME,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request": {
            "traces": str(traces),
            "output": str(output),
            "provider": provider,
            "model": model,
            "options": {
                "max_rounds": max_rounds,
                "kappa_target": kappa_target,
                "coverage_floor": coverage_floor,
                "no_early_stop": no_early_stop,
                "max_output_tokens": max_output_tokens,
                "aws_region": aws_region,
                "aws_profile": aws_profile,
            },
        },
        "model_provider": {
            "provider": provider,
            "model": model,
            "max_output_tokens": max_output_tokens,
        },
        "acceptance": {
            "kappa_metric": "macro Fleiss kappa over used codes",
            "kappa_target": kappa_target,
            "coverage_floor": coverage_floor,
            "final_kappa": final_kappa,
            "final_coverage": final_coverage,
            "passed": accepted,
        },
        "trace_input": bundle.report(),
        "agreement": agreement_summary,
        "artifacts": {
            "normalized_traces": _relative(normalized_path, output),
            "trace_report": _relative(trace_report_path, output),
            "draft_taxonomy": _relative(draft_path, output),
            "agreement_input": _relative(agreement_input_path, output),
            "final_taxonomy": _relative(taxonomy_path, output),
        },
    }
    _write_json(output / "manifest.json", manifest)

    from adamast.dashboard.viewer import render_taxonomy_html

    render_taxonomy_html(
        taxonomy_path, manifest=manifest, open_browser=open_viewer
    )
    return taxonomy


def prepare_taxonomy_for_agreement(draft: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt the layered draft schema to the agreement program's schema."""

    full_layer = draft.get("full_layer")
    if not isinstance(full_layer, Mapping):
        raise ValueError("draft taxonomy has no full_layer for agreement")

    categories: dict[str, Any] = {}
    for category in ("category_a", "category_b", "category_c"):
        codes = full_layer.get(category)
        if not isinstance(codes, Mapping):
            raise ValueError(f"draft taxonomy full_layer.{category} is missing")
        categories[category] = json.loads(json.dumps(codes))

    if not any(categories.values()):
        raise ValueError("draft taxonomy contains no codes for agreement")

    return {
        "metadata": {
            **dict(draft.get("metadata") or {}),
            "strategy": PIPELINE_NAME,
            "source_schema": "adamast-layered-draft",
        },
        "category_definitions": dict(draft.get("category_definitions") or {}),
        "role_definitions": dict(draft.get("role_definitions") or {}),
        **categories,
    }


def build_public_taxonomy(
    refined: Mapping[str, Any],
    *,
    draft: Mapping[str, Any],
    status: str,
    provider: str,
    model: str,
    trace_count: int,
    agreement_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the stable, integration-neutral AdaMAST taxonomy document."""

    codes: list[dict[str, Any]] = []
    for key, category in (
        ("category_a", "A"),
        ("category_b", "B"),
        ("category_c", "C"),
    ):
        raw_codes = refined.get(key) or {}
        items = raw_codes.items() if isinstance(raw_codes, Mapping) else enumerate(raw_codes)
        for fallback_id, raw in items:
            if not isinstance(raw, Mapping):
                continue
            code_id = str(raw.get("code") or raw.get("id") or fallback_id)
            entry = {
                "id": code_id,
                "name": str(raw.get("name") or "Unnamed failure mode"),
                "description": str(
                    raw.get("definition") or raw.get("description") or ""
                ),
                "category": category,
            }
            for field in (
                "severity",
                "when_to_use",
                "when_not_to_use",
                "applies_to_role",
                "examples",
            ):
                if raw.get(field) not in (None, "", []):
                    entry[field] = raw[field]
            codes.append(entry)

    domain_info = (
        draft.get("full_layer", {}).get("domain_info", {})
        if isinstance(draft.get("full_layer"), Mapping)
        else {}
    )
    if isinstance(domain_info, Mapping):
        domain = str(
            domain_info.get("domain")
            or domain_info.get("task_domain")
            or domain_info.get("name")
            or "Generated agent traces"
        )
        domain_summary = str(
            domain_info.get("description")
            or domain_info.get("summary")
            or ""
        )
    else:
        domain = str(domain_info or "Generated agent traces")
        domain_summary = ""

    return {
        "schema_version": 1,
        "strategy": PIPELINE_NAME,
        "status": status,
        "display_name": f"{domain} failure taxonomy",
        "domain": domain,
        "summary": domain_summary
        or f"Failure modes generated and agreement-checked from {trace_count} traces.",
        "codes": sorted(codes, key=lambda item: item["id"]),
        "generation": {
            "provider": provider,
            "model": model,
            "trace_count": trace_count,
            "agreement": dict(agreement_summary),
        },
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
