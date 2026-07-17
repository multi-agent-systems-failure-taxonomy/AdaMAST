"""Command-line interface for AdaMAST."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .generation import GenerationRequest
from .generation.baseline import BaselineStrategy
from .generation.providers import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    SUPPORTED_PROVIDERS,
    normalize_provider_name,
    resolve_model,
    validate_provider_credentials,
)
from .generation.traces import (
    TraceFormatError,
    load_trace_bundle,
    write_normalized_jsonl,
)
from .viewer import render_taxonomy_html


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adamast")
    commands = parser.add_subparsers(dest="command", required=True)

    traces = commands.add_parser("traces", help="validate or normalize trace inputs")
    trace_commands = traces.add_subparsers(dest="trace_command", required=True)
    validate = trace_commands.add_parser("validate", help="validate accepted trace formats")
    validate.add_argument("source", type=Path)
    normalize = trace_commands.add_parser(
        "normalize", help="write canonical AdaMAST JSONL"
    )
    normalize.add_argument("source", type=Path)
    normalize.add_argument("--output", type=Path, required=True)

    taxonomy = commands.add_parser("taxonomy", help="generate or view taxonomies")
    taxonomy_commands = taxonomy.add_subparsers(
        dest="taxonomy_command", required=True
    )
    generate = taxonomy_commands.add_parser(
        "generate", help="run a named taxonomy-generation strategy"
    )
    generate.add_argument("--strategy", choices=["baseline"], default="baseline")
    generate.add_argument("--traces", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default=os.getenv("ADAMAST_PROVIDER"),
        help="model API transport; generation prompts are unchanged",
    )
    generate.add_argument(
        "--model",
        help="provider model ID; provider-specific environment variables are supported",
    )
    generate.add_argument(
        "--max-output-tokens",
        type=int,
        default=DEFAULT_MAX_OUTPUT_TOKENS,
        help="maximum output tokens for each model call",
    )
    generate.add_argument(
        "--aws-region",
        default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
        help="Bedrock region; otherwise use the AWS configuration chain",
    )
    generate.add_argument(
        "--aws-profile",
        default=os.getenv("AWS_PROFILE"),
        help="optional AWS profile for Bedrock",
    )
    generate.add_argument("--max-rounds", type=int, default=5)
    generate.add_argument("--kappa-target", type=float, default=0.75)
    generate.add_argument("--coverage-floor", type=float, default=0.70)
    generate.add_argument("--no-early-stop", action="store_true")
    generate.add_argument(
        "--view",
        action="store_true",
        help="create and open the read-only taxonomy field guide",
    )

    view = taxonomy_commands.add_parser(
        "view", help="open one taxonomy as a read-only browser field guide"
    )
    view.add_argument("taxonomy", type=Path)
    view.add_argument("--manifest", type=Path)
    view.add_argument("--output", type=Path)
    view.add_argument("--no-open", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "traces":
            return _run_traces(args)
        if args.taxonomy_command == "view":
            path = render_taxonomy_html(
                args.taxonomy,
                manifest=args.manifest,
                output=args.output,
                open_browser=not args.no_open,
            )
            print(f"AdaMAST taxonomy view: {path}")
            return 0
        return _run_generation(args)
    except (TraceFormatError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run_traces(args: argparse.Namespace) -> int:
    bundle = load_trace_bundle(args.source)
    if args.trace_command == "normalize":
        output = write_normalized_jsonl(bundle.traces, args.output)
        print(f"Normalized {len(bundle.traces)} traces: {output}")
    print(json.dumps(bundle.report(), indent=2, ensure_ascii=False))
    return 0


def _run_generation(args: argparse.Namespace) -> int:
    provider = normalize_provider_name(args.provider)
    model = resolve_model(provider, args.model)
    validate_provider_credentials(provider)
    strategy = BaselineStrategy()
    result = strategy.generate(
        GenerationRequest(
            traces=args.traces,
            output=args.output,
            provider=provider,
            model=model,
            open_viewer=args.view,
            options={
                "max_rounds": args.max_rounds,
                "kappa_target": args.kappa_target,
                "coverage_floor": args.coverage_floor,
                "no_early_stop": args.no_early_stop,
                "max_output_tokens": args.max_output_tokens,
                "aws_region": args.aws_region,
                "aws_profile": args.aws_profile,
            },
        )
    )
    print(f"BASELINE status: {result.status}")
    print(f"Taxonomy: {result.taxonomy_path}")
    print(f"Agreement manifest: {result.manifest_path}")
    print(f"Browser view: {result.viewer_path}")
    return 0 if result.accepted else 3


if __name__ == "__main__":
    raise SystemExit(main())
