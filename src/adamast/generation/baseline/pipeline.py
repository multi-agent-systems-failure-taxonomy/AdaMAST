"""Orchestration for the named BASELINE generation strategy."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from ..providers import DEFAULT_MAX_OUTPUT_TOKENS, create_provider
from ..protocols import GenerationRequest, GenerationResult
from ..traces import load_trace_bundle, write_normalized_jsonl
from .agreement import Config as AgreementConfig
from .agreement import TaxonomyRefinerPipeline
from .draft import Config as DraftConfig
from .draft import LLMNomos


STRATEGY_NAME = "baseline"
DEFAULT_KAPPA_TARGET = 0.75
DEFAULT_COVERAGE_FLOOR = 0.70
DEFAULT_MAX_ROUNDS = 5


class BaselineStrategy:
    """Basic draft generation followed by the full agreement gate."""

    name = STRATEGY_NAME

    def generate(self, request: GenerationRequest) -> GenerationResult:
        output = request.output.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        artifacts = output / "artifacts"
        draft_dir = artifacts / "draft"
        agreement_dir = artifacts / "agreement"
        inputs_dir = artifacts / "inputs"
        for directory in (draft_dir, agreement_dir, inputs_dir):
            directory.mkdir(parents=True, exist_ok=True)

        bundle = load_trace_bundle(request.traces)
        normalized_path = write_normalized_jsonl(
            bundle.traces, inputs_dir / "traces.normalized.jsonl"
        )
        trace_report_path = _write_json(
            inputs_dir / "trace_report.json", bundle.report()
        )

        max_rounds = int(request.options.get("max_rounds", DEFAULT_MAX_ROUNDS))
        kappa_target = float(
            request.options.get("kappa_target", DEFAULT_KAPPA_TARGET)
        )
        coverage_floor = float(
            request.options.get("coverage_floor", DEFAULT_COVERAGE_FLOOR)
        )
        no_early_stop = bool(request.options.get("no_early_stop", False))
        max_output_tokens = int(
            request.options.get(
                "max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS
            )
        )
        aws_region = _optional_string(request.options.get("aws_region"))
        aws_profile = _optional_string(request.options.get("aws_profile"))

        DraftConfig.PROVIDER = request.provider
        DraftConfig.MODEL = request.model
        draft_provider = create_provider(
            request.provider,
            request.model,
            timeout=DraftConfig.TIMEOUT,
            max_output_tokens=max_output_tokens,
            aws_region=aws_region,
            aws_profile=aws_profile,
        )
        draft = LLMNomos(
            normalized_path, draft_dir, client=draft_provider
        ).run()
        if not isinstance(draft, dict):
            raise RuntimeError("BASELINE draft generation did not return a taxonomy")

        draft_path = _write_json(output / "taxonomy.draft.json", draft)
        agreement_input = prepare_taxonomy_for_agreement(draft)
        agreement_input_path = _write_json(
            agreement_dir / "taxonomy.input.json", agreement_input
        )

        AgreementConfig.PROVIDER = request.provider
        AgreementConfig.MODEL = request.model
        AgreementConfig.MAX_ROUNDS = max_rounds
        AgreementConfig.KAPPA_TARGET = kappa_target
        AgreementConfig.COVERAGE_FLOOR = coverage_floor
        AgreementConfig.NO_EARLY_STOP = no_early_stop

        agreement_provider = create_provider(
            request.provider,
            request.model,
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
            raise RuntimeError("BASELINE agreement process did not return a result")

        final_kappa = float(agreement_summary.get("final_kappa") or 0.0)
        final_coverage = float(agreement_summary.get("final_coverage") or 0.0)
        accepted = (
            final_kappa >= kappa_target and final_coverage >= coverage_floor
        )
        status = "accepted" if accepted else "review_required"

        taxonomy = build_public_taxonomy(
            agreement.taxonomy,
            draft=draft,
            status=status,
            provider=request.provider,
            model=request.model,
            trace_count=len(bundle.traces),
            agreement_summary=agreement_summary,
        )
        taxonomy_path = _write_json(output / "taxonomy.json", taxonomy)

        manifest = {
            "schema_version": 1,
            "strategy": STRATEGY_NAME,
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "request": {
                **asdict(request),
                "traces": str(request.traces),
                "output": str(request.output),
                "options": dict(request.options),
            },
            "model_provider": {
                "provider": request.provider,
                "model": request.model,
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
        manifest_path = _write_json(output / "manifest.json", manifest)

        from ...viewer import render_taxonomy_html

        viewer_path = render_taxonomy_html(
            taxonomy_path, manifest=manifest, open_browser=request.open_viewer
        )
        return GenerationResult(
            strategy=STRATEGY_NAME,
            status=status,
            taxonomy_path=taxonomy_path,
            manifest_path=manifest_path,
            viewer_path=viewer_path,
            summary=agreement_summary,
        )


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
            "strategy": STRATEGY_NAME,
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
        "strategy": STRATEGY_NAME,
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


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
