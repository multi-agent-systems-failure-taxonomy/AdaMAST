from __future__ import annotations

import io
import sys
from unittest.mock import patch

from adamast.cli import build_parser, main


def test_generate_parser_exposes_provider_model_and_bedrock_options() -> None:
    args = build_parser().parse_args(
        [
            "generate",
            "--provider",
            "bedrock",
            "--model",
            "provider.model-id",
            "--aws-region",
            "us-west-2",
            "--aws-profile",
            "research",
            "--traces",
            "traces.jsonl",
            "--output",
            "run",
        ]
    )

    assert args.provider == "bedrock"
    assert args.model == "provider.model-id"
    assert args.aws_region == "us-west-2"
    assert args.aws_profile == "research"


@patch("adamast.cli.generate_taxonomy")
def test_cli_passes_provider_configuration_to_generation(
    generate,
) -> None:
    generate.return_value = {"status": "accepted"}

    exit_code = main(
        [
            "generate",
            "--provider",
            "anthropic",
            "--model",
            "model-a",
            "--max-output-tokens",
            "4096",
            "--traces",
            "traces.jsonl",
            "--output",
            "run",
        ]
    )

    assert exit_code == 0
    kwargs = generate.call_args.kwargs
    assert kwargs["provider"] == "anthropic"
    assert kwargs["model"] == "model-a"
    assert kwargs["max_output_tokens"] == 4096


def test_cli_reports_missing_provider_credentials(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    exit_code = main(
        [
            "generate",
            "--provider",
            "google",
            "--model",
            "model-g",
            "--traces",
            "traces.jsonl",
            "--output",
            "run",
        ]
    )

    assert exit_code == 2
    assert "GEMINI_API_KEY or GOOGLE_API_KEY" in capsys.readouterr().err


def test_main_forces_utf8_output_on_legacy_console(
    monkeypatch,
    tmp_path,
) -> None:
    legacy_out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    legacy_err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", legacy_out)
    monkeypatch.setattr(sys, "stderr", legacy_err)

    traces = tmp_path / "traces.jsonl"
    traces.write_text(
        '{"trace_id":"t-1","messages":[{"role":"user","content":"2+2?"},'
        '{"role":"assistant","content":"4"}]}\n',
        encoding="utf-8",
    )

    exit_code = main(["validate", str(traces)])

    assert exit_code == 0
    assert legacy_out.encoding.lower() == "utf-8"
    assert legacy_err.encoding.lower() == "utf-8"
    # The exact line that crashed generation on cp1252 consoles must print.
    print("─" * 40)
    legacy_out.flush()


def test_cli_requires_explicit_provider(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("ADAMAST_PROVIDER", raising=False)

    exit_code = main(
        [
            "generate",
            "--traces",
            "traces.jsonl",
            "--output",
            "run",
        ]
    )

    assert exit_code == 2
    assert "--provider or ADAMAST_PROVIDER" in capsys.readouterr().err
