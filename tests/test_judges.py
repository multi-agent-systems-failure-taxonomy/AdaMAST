from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from adamast.judges.contract import (
    JUDGE_SYSTEM_PROMPT,
    Diagnosis,
    JudgeResponseError,
    SelectionDiagnosis,
    SelectionTraceJudge,
    TaxonomyJudge,
    create_judge,
    judge_trace,
    judge_traces,
)


class _StubProvider:
    name = "stub"
    model = "stub-model"

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        response_format: str = "text",
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "response_format": response_format,
            }
        )
        return self.responses.pop(0)


def _taxonomy(*, status: str = "accepted") -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "codes": [
            {
                "id": "A.1",
                "name": "Missing evidence",
                "description": "The conclusion has no supporting evidence.",
                "category": "A",
                "when_to_use": "The trace makes an unsupported claim.",
                "when_not_to_use": "The evidence exists but is misread.",
            },
            {
                "id": "B.1",
                "name": "Checker accepts weak support",
                "description": "The checker approves inadequate evidence.",
                "category": "B",
            },
        ],
    }


def _trace(trace_id: str = "trace-1") -> dict:
    return {
        "problem_id": trace_id,
        "task": "Answer with evidence",
        "raw_trajectory": "The agent states a conclusion without a source.",
        "metadata": {"mas_name": "demo", "llm_name": "test-model"},
    }


def test_judge_preserves_prompt_and_returns_canonical_taxonomy_fields() -> None:
    provider = _StubProvider(
        json.dumps(
            {
                "code": "A.1",
                "label": "model-provided label must not override taxonomy",
                "evidence": "states a conclusion without a source",
                "confidence": 0.87,
                "recovery_hint": "Collect a supporting source.",
            }
        )
    )

    diagnosis = TaxonomyJudge(_taxonomy(), provider).judge(_trace())

    assert diagnosis.to_dict() == {
        "trace_id": "trace-1",
        "code": "A.1",
        "label": "Missing evidence",
        "category": "A",
        "evidence": "states a conclusion without a source",
        "confidence": 0.87,
        "recovery_hint": "Collect a supporting source.",
    }
    assert provider.calls[0]["system"] == JUDGE_SYSTEM_PROMPT
    assert provider.calls[0]["response_format"] == "json"
    assert "Classify the following agent failure trace into ONE taxonomy code." in (
        provider.calls[0]["prompt"]
    )
    assert "Use when: The trace makes an unsupported claim." in (
        provider.calls[0]["prompt"]
    )


def test_judge_many_preserves_trace_order() -> None:
    provider = _StubProvider(
        '{"code":"A.1","confidence":0.6}',
        '{"code":"B.1","confidence":0.7}',
    )
    judge = TaxonomyJudge(_taxonomy(), provider)

    diagnoses = judge.judge_many([_trace("first"), _trace("second")])

    assert [item.trace_id for item in diagnoses] == ["first", "second"]
    assert [item.code for item in diagnoses] == ["A.1", "B.1"]


def test_unknown_code_is_not_silently_replaced() -> None:
    provider = _StubProvider('{"code":"A.99","confidence":0.5}')

    with pytest.raises(JudgeResponseError, match="unknown taxonomy code"):
        TaxonomyJudge(_taxonomy(), provider).judge(_trace())


@pytest.mark.parametrize(
    "response, message",
    [
        ("not json", "did not contain a JSON object"),
        ('{"code":"A.1","confidence":"high"}', "invalid confidence"),
        ('{"code":"A.1","confidence":1.1}', "between 0 and 1"),
    ],
)
def test_malformed_judge_output_fails_explicitly(
    response: str, message: str
) -> None:
    with pytest.raises(JudgeResponseError, match=message):
        TaxonomyJudge(_taxonomy(), _StubProvider(response)).judge(_trace())


def test_review_required_taxonomy_needs_explicit_override() -> None:
    with pytest.raises(ValueError, match="review_required"):
        TaxonomyJudge(
            _taxonomy(status="review_required"),
            _StubProvider('{"code":"A.1"}'),
        )

    judge = TaxonomyJudge(
        _taxonomy(status="review_required"),
        _StubProvider('{"code":"A.1"}'),
        allow_review_required=True,
    )
    assert judge.judge(_trace()).code == "A.1"


def test_legacy_atlas_layered_taxonomy_is_accepted_during_migration() -> None:
    legacy = {
        "full_layer": {
            "category_a": {
                "A.7": {
                    "code": "A.7",
                    "name": "Legacy code",
                    "definition": "A legacy layered definition.",
                }
            },
            "category_b": {},
            "category_c": {},
        }
    }
    judge = TaxonomyJudge(
        legacy, _StubProvider('{"code":"A.7","confidence":0.9}')
    )

    assert judge.judge(_trace()).label == "Legacy code"


@patch("adamast.judges.contract.create_provider")
def test_create_judge_uses_shared_provider_configuration(
    provider_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    adapter = _StubProvider('{"code":"A.1"}')
    provider_factory.return_value = adapter

    judge = create_judge(
        _taxonomy(),
        provider="anthropic",
        model="model-a",
        max_trace_chars=4096,
        max_output_tokens=2048,
    )

    assert judge.provider is adapter
    provider_factory.assert_called_once_with(
        "anthropic",
        "model-a",
        max_output_tokens=2048,
        aws_region=None,
        aws_profile=None,
    )


@patch("adamast.judges.contract.create_provider")
def test_judge_traces_loads_every_trace_from_a_file(
    provider_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    provider_factory.return_value = _StubProvider(
        '{"failure_modes":[{"code":"A.1","evidence":"e","reason":"r"}],'
        '"none_apply":false}',
        '{"failure_modes":[],"none_apply":true}',
    )
    source = tmp_path / "traces.jsonl"
    source.write_text(
        '{"trace_id":"one","raw_trajectory":"first"}\n'
        '{"trace_id":"two","raw_trajectory":"second"}\n',
        encoding="utf-8",
    )

    diagnoses = judge_traces(
        _taxonomy(),
        source,
        provider="openai",
        model="model-o",
    )

    assert [item.trace_id for item in diagnoses] == ["one", "two"]
    assert diagnoses[0].code_ids() == ["A.1"]
    assert diagnoses[1].none_apply is True


def test_trace_sampling_keeps_beginning_and_end() -> None:
    trajectory = "BEGIN-" + ("x" * 8000) + "-END"
    provider = _StubProvider('{"code":"A.1"}')

    TaxonomyJudge(
        _taxonomy(), provider, max_trace_chars=1000
    ).judge(
        {
            "problem_id": "long",
            "raw_trajectory": trajectory,
        }
    )

    prompt = provider.calls[0]["prompt"]
    assert "BEGIN-" in prompt
    assert "-END" in prompt
    assert "[TRUNCATED]" in prompt


def test_default_judge_selects_every_supported_code() -> None:
    provider = _StubProvider(
        '{"failure_modes":[{"code":"A.1","evidence":"no source cited",'
        '"reason":"claim lacks support"},{"code":"B.1","evidence":"checker '
        'approved","reason":"weak support accepted"}],"none_apply":false}'
    )

    diagnosis = SelectionTraceJudge(_taxonomy(), provider).judge(_trace())

    assert isinstance(diagnosis, SelectionDiagnosis)
    assert diagnosis.trace_id == "trace-1"
    assert diagnosis.code_ids() == ["A.1", "B.1"]
    assert diagnosis.none_apply is False
    assert provider.calls[0]["response_format"] == "json"


def test_default_judge_none_apply_is_a_valid_result() -> None:
    provider = _StubProvider('{"failure_modes":[],"none_apply":true}')

    diagnosis = SelectionTraceJudge(_taxonomy(), provider).judge(_trace())

    assert diagnosis.none_apply is True
    assert diagnosis.failure_modes == []
    assert diagnosis.code_ids() == []


def test_default_judge_drops_alien_codes_with_warning() -> None:
    provider = _StubProvider(
        '{"failure_modes":[{"code":"Z.9","evidence":"e","reason":"r"},'
        '{"code":"A.1","evidence":"e","reason":"r"}],"none_apply":false}'
    )

    diagnosis = SelectionTraceJudge(_taxonomy(), provider).judge(_trace())

    assert diagnosis.code_ids() == ["A.1"]
    assert diagnosis.judge_metadata["warnings"]


def test_default_judge_honors_review_required_gate() -> None:
    with pytest.raises(ValueError):
        SelectionTraceJudge(
            _taxonomy(status="review_required"), _StubProvider()
        )

    judge = SelectionTraceJudge(
        _taxonomy(status="review_required"),
        _StubProvider('{"failure_modes":[],"none_apply":true}'),
        allow_review_required=True,
    )
    assert judge.judge(_trace()).none_apply is True


def test_default_judge_serializes_to_dict() -> None:
    provider = _StubProvider(
        '{"failure_modes":[{"code":"A.1","evidence":"e","reason":"r"}],'
        '"none_apply":false}'
    )

    payload = SelectionTraceJudge(_taxonomy(), provider).judge(_trace()).to_dict()

    assert payload["trace_id"] == "trace-1"
    assert payload["failure_modes"][0]["code"] == "A.1"
    assert payload["none_apply"] is False
    assert "judge_metadata" in payload


@patch("adamast.judges.contract.create_provider")
def test_create_judge_mode_selects_judge_class(
    provider_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    provider_factory.return_value = _StubProvider()

    default_judge = create_judge(_taxonomy(), provider="openai", model="m")
    single_judge = create_judge(
        _taxonomy(), provider="openai", model="m", mode="single"
    )

    assert isinstance(default_judge, SelectionTraceJudge)
    assert isinstance(single_judge, TaxonomyJudge)
    with pytest.raises(ValueError):
        create_judge(_taxonomy(), provider="openai", model="m", mode="both")


@patch("adamast.judges.contract.create_provider")
def test_judge_trace_mode_controls_result_shape(
    provider_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    provider_factory.return_value = _StubProvider(
        '{"failure_modes":[],"none_apply":true}'
    )
    default_result = judge_trace(
        _taxonomy(), _trace(), provider="openai", model="m"
    )
    assert isinstance(default_result, SelectionDiagnosis)

    provider_factory.return_value = _StubProvider('{"code":"A.1"}')
    single_result = judge_trace(
        _taxonomy(), _trace(), provider="openai", model="m", mode="single"
    )
    assert isinstance(single_result, Diagnosis)
    assert single_result.code == "A.1"
