"""Command-line interface for AdaMAST: ``adamast`` / ``python -m adamast``."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .api import generate_taxonomy
from .providers import DEFAULT_MAX_OUTPUT_TOKENS, SUPPORTED_PROVIDERS
from .traces import (
    TraceFormatError,
    load_trace_bundle,
    write_normalized_jsonl,
)
from .viewer import render_taxonomy_html


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adamast")
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser(
        "generate", help="generate an agreement-gated taxonomy from traces"
    )
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

    validate = commands.add_parser(
        "validate", help="validate accepted trace formats"
    )
    validate.add_argument("source", type=Path)

    normalize = commands.add_parser(
        "normalize", help="write canonical AdaMAST JSONL"
    )
    normalize.add_argument("source", type=Path)
    normalize.add_argument("--output", type=Path, required=True)

    view = commands.add_parser(
        "view", help="open one taxonomy as a read-only browser field guide"
    )
    view.add_argument("taxonomy", type=Path)
    view.add_argument("--manifest", type=Path)
    view.add_argument("--output", type=Path)
    view.add_argument("--no-open", action="store_true")
    return parser


def _force_utf8_output() -> None:
    """Keep console output from crashing on non-ASCII characters.

    Windows defaults redirected streams to a legacy code page such as cp1252,
    which cannot encode every character the pipeline prints (progress rules,
    model-written taxonomy text). UTF-8 can encode all of them.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            bundle = load_trace_bundle(args.source)
            print(json.dumps(bundle.report(), indent=2, ensure_ascii=False))
            return 0
        if args.command == "normalize":
            bundle = load_trace_bundle(args.source)
            output = write_normalized_jsonl(bundle.traces, args.output)
            print(f"Normalized {len(bundle.traces)} traces: {output}")
            print(json.dumps(bundle.report(), indent=2, ensure_ascii=False))
            return 0
        if args.command == "view":
            path = render_taxonomy_html(
                args.taxonomy,
                manifest=args.manifest,
                output=args.output,
                open_browser=not args.no_open,
            )
            print(f"AdaMAST taxonomy view: {path}")
            return 0
        return _run_generate(args)
    except (TraceFormatError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run_generate(args: argparse.Namespace) -> int:
    output = Path(args.output)
    taxonomy = generate_taxonomy(
        args.traces,
        output,
        provider=args.provider,
        model=args.model,
        max_rounds=args.max_rounds,
        kappa_target=args.kappa_target,
        coverage_floor=args.coverage_floor,
        no_early_stop=args.no_early_stop,
        max_output_tokens=args.max_output_tokens,
        aws_region=(args.aws_region or "").strip() or None,
        aws_profile=(args.aws_profile or "").strip() or None,
        open_viewer=args.view,
    )
    status = taxonomy["status"]
    output = output.expanduser().resolve()
    print(f"Status: {status}")
    print(f"Taxonomy: {output / 'taxonomy.json'}")
    print(f"Agreement manifest: {output / 'manifest.json'}")
    print(f"Browser view: {output / 'taxonomy.html'}")
    return 0 if status == "accepted" else 3


if __name__ == "__main__":
    raise SystemExit(main())
