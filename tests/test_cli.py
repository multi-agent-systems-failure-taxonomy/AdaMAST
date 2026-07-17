from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from adamast.cli import build_parser, main


def test_generate_parser_exposes_provider_model_and_bedrock_options() -> None:
    args = build_parser().parse_args(
        [
            "taxonomy",
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


@patch("adamast.cli.BaselineStrategy")
def test_cli_passes_provider_configuration_to_generation(
    strategy_class,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    strategy_class.return_value.generate.return_value = SimpleNamespace(
        status="accepted",
        taxonomy_path=Path("taxonomy.json"),
        manifest_path=Path("manifest.json"),
        viewer_path=Path("taxonomy.html"),
        accepted=True,
    )

    exit_code = main(
        [
            "taxonomy",
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
    request = strategy_class.return_value.generate.call_args.args[0]
    assert request.provider == "anthropic"
    assert request.model == "model-a"
    assert request.options["max_output_tokens"] == 4096


def test_cli_reports_missing_provider_credentials(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    exit_code = main(
        [
            "taxonomy",
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


def test_cli_requires_explicit_provider(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("ADAMAST_PROVIDER", raising=False)

    exit_code = main(
        [
            "taxonomy",
            "generate",
            "--traces",
            "traces.jsonl",
            "--output",
            "run",
        ]
    )

    assert exit_code == 2
    assert "--provider or ADAMAST_PROVIDER" in capsys.readouterr().err
