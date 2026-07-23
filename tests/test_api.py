from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from adamast.learning.api import (
    build_public_taxonomy,
    generate_taxonomy,
    prepare_taxonomy_for_agreement,
)


def _draft_taxonomy() -> dict:
    return {
        "metadata": {"version": "13.0"},
        "category_definitions": {"A": "System", "B": "Role", "C": "Domain"},
        "full_layer": {
            "domain_info": {
                "domain": "Demo agents",
                "description": "Failures in a small demonstration system.",
            },
            "category_a": {
                "A.1": {
                    "code": "A.1",
                    "name": "Empty output",
                    "definition": "The agent returned no usable output.",
                }
            },
            "category_b": {},
            "category_c": {},
        },
    }


def test_adapts_layered_draft_for_agreement() -> None:
    adapted = prepare_taxonomy_for_agreement(_draft_taxonomy())

    assert adapted["category_a"]["A.1"]["name"] == "Empty output"
    assert adapted["metadata"]["strategy"] == "baseline"
    assert "full_layer" not in adapted


def test_builds_integration_neutral_taxonomy() -> None:
    public = build_public_taxonomy(
        prepare_taxonomy_for_agreement(_draft_taxonomy()),
        draft=_draft_taxonomy(),
        status="accepted",
        provider="anthropic",
        model="test-model",
        trace_count=4,
        agreement_summary={"final_kappa": 0.81, "final_coverage": 0.9},
    )

    assert public["strategy"] == "baseline"
    assert public["status"] == "accepted"
    assert public["domain"] == "Demo agents"
    assert public["generation"]["provider"] == "anthropic"
    assert public["codes"] == [
        {
            "id": "A.1",
            "name": "Empty output",
            "description": "The agent returned no usable output.",
            "category": "A",
        }
    ]


@patch("adamast.learning.api.create_provider")
@patch("adamast.learning.api.TaxonomyRefinerPipeline")
@patch("adamast.learning.api.LLMNomos")
def test_generate_persists_gate_and_browser_artifacts(
    draft_class, agreement_class, provider_factory, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    traces = tmp_path / "traces.jsonl"
    traces.write_text(
        '{"trace_id":"one","raw_trajectory":"agent output"}\n',
        encoding="utf-8",
    )
    draft_class.return_value.run.return_value = _draft_taxonomy()
    agreement_instance = agreement_class.return_value
    agreement_instance.run.return_value = {
        "final_kappa": 0.80,
        "final_coverage": 0.75,
        "rounds_completed": 3,
    }
    agreement_instance.taxonomy = prepare_taxonomy_for_agreement(_draft_taxonomy())
    draft_provider = object()
    agreement_provider = object()
    provider_factory.side_effect = [draft_provider, agreement_provider]

    output = tmp_path / "run"
    taxonomy = generate_taxonomy(
        traces, output, provider="anthropic", model="test-model"
    )

    assert taxonomy["status"] == "accepted"
    assert (output / "taxonomy.json").exists()
    assert (output / "taxonomy.html").exists()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "accepted"
    assert manifest["acceptance"]["kappa_metric"].startswith("macro Fleiss")
    assert manifest["trace_input"]["formats"] == {"adamast": 1}
    assert manifest["model_provider"] == {
        "provider": "anthropic",
        "model": "test-model",
        "max_output_tokens": 8192,
    }
    assert draft_class.call_args.kwargs["client"] is draft_provider
    assert agreement_class.call_args.kwargs["client"] is agreement_provider


@patch("adamast.learning.api.create_provider")
@patch("adamast.learning.api.TaxonomyRefinerPipeline")
@patch("adamast.learning.api.LLMNomos")
def test_generate_never_marks_failed_gate_accepted(
    draft_class, agreement_class, provider_factory, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    traces = tmp_path / "traces.jsonl"
    traces.write_text(
        '{"trace_id":"one","raw_trajectory":"agent output"}\n',
        encoding="utf-8",
    )
    draft_class.return_value.run.return_value = _draft_taxonomy()
    agreement_instance = agreement_class.return_value
    agreement_instance.run.return_value = {
        "final_kappa": 0.74,
        "final_coverage": 0.95,
        "rounds_completed": 5,
    }
    agreement_instance.taxonomy = prepare_taxonomy_for_agreement(_draft_taxonomy())
    provider_factory.side_effect = [object(), object()]

    output = tmp_path / "run"
    taxonomy = generate_taxonomy(
        traces, output, provider="openai", model="test-model"
    )

    assert taxonomy["status"] == "review_required"
    on_disk = json.loads((output / "taxonomy.json").read_text(encoding="utf-8"))
    assert on_disk["status"] == "review_required"
