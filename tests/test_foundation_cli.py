"""Foundation verbs restored to the umbrella CLI: generate, judge,
validate, normalize, view.

These commands are the standalone workflow the public docs teach; they were
lost when the public staging fork merged, so every test here guards the
doc-facing surface, not internals.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from adamast import cli as umbrella
from adamast import foundation_cli

FIXTURE_TAXONOMY = (
    Path(__file__).resolve().parent / "fixtures" / "taxonomies" / "tax-django-orm-001.json"
)


def _trace_file(tmp_path: Path) -> Path:
    source = tmp_path / "traces.json"
    source.write_text(
        json.dumps(
            {
                "trace_id": "t1",
                "task": "demo",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "done"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return source


class FoundationCliTests:
    pass


def test_validate_reports_trace_count(tmp_path, capsys):
    code = foundation_cli.main(["validate", str(_trace_file(tmp_path))])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["trace_count"] == 1


def test_normalize_writes_canonical_jsonl(tmp_path, capsys):
    output = tmp_path / "out.jsonl"
    code = foundation_cli.main(
        ["normalize", str(_trace_file(tmp_path)), "--output", str(output)]
    )
    assert code == 0
    lines = output.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["problem_id"] == "t1"


def test_view_renders_field_guide_without_browser(tmp_path):
    output = tmp_path / "view.html"
    code = foundation_cli.main(
        ["view", str(FIXTURE_TAXONOMY), "--no-open", "--output", str(output)]
    )
    assert code == 0
    assert "<html" in output.read_text(encoding="utf-8").lower()


def test_generate_wires_flags_and_maps_status_to_exit_code(tmp_path):
    calls = {}

    def fake_generate(traces, output, **kwargs):
        calls["traces"] = traces
        calls["kwargs"] = kwargs
        return {"status": "review_required"}

    with patch.object(foundation_cli, "generate_taxonomy", fake_generate):
        code = foundation_cli.main(
            [
                "generate",
                "--traces",
                str(_trace_file(tmp_path)),
                "--output",
                str(tmp_path / "tax"),
                "--kappa-target",
                "0.8",
            ]
        )
    assert code == 3  # review_required is a non-accepted status
    assert calls["kwargs"]["kappa_target"] == 0.8


def test_judge_writes_output_payload(tmp_path):
    fake_judge = SimpleNamespace(
        provider=SimpleNamespace(name="stub", model="stub-model"),
        judge_many=lambda traces: [
            SimpleNamespace(to_dict=lambda: {"trace_id": "t1", "code": "A.1"})
        ],
    )
    output = tmp_path / "judgments.json"
    with patch.object(foundation_cli, "create_judge", lambda *a, **k: fake_judge):
        code = foundation_cli.main(
            [
                "judge",
                "--taxonomy",
                str(FIXTURE_TAXONOMY),
                "--traces",
                str(_trace_file(tmp_path)),
                "--output",
                str(output),
            ]
        )
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["trace_count"] == 1
    assert payload["diagnoses"][0]["code"] == "A.1"


def test_umbrella_dispatches_every_foundation_verb(tmp_path, capsys):
    code = umbrella.main(["validate", str(_trace_file(tmp_path))])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["trace_count"] == 1
    for verb in ("generate", "judge", "validate", "normalize", "view"):
        with pytest.raises(SystemExit) as excinfo:
            umbrella.main([verb, "--help"])
        assert excinfo.value.code == 0
