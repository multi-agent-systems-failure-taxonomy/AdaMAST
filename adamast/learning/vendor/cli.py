"""Command-line interface.

Two subcommands:

- ``adamast generate`` — generate a taxonomy from traces
- ``adamast classify`` — classify a trace against an existing taxonomy

Example::

    python -m adamast generate --traces traces.jsonl --output ./my_tax
    python -m adamast classify --taxonomy ./my_tax/taxonomy.json --trace one.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adamast.learning.vendor.api import classify_trace, generate_taxonomy
from adamast.learning.vendor.traces.loader import load_traces


def _add_generate_parser(sub) -> None:
    p = sub.add_parser("generate", help="Generate a taxonomy from a set of traces")
    p.add_argument("--traces", required=True, help="Path to a trace file or directory")
    p.add_argument("--output", "--output-dir", dest="output", default="./adamast_output",
                   help="Directory for taxonomy.json + intermediate step files")
    p.add_argument("--model", default=None, help="Override the LLM model (env ADAMAST_MODEL also works)")
    p.add_argument("--max-codes", type=int, default=0,
                   help="Cap total codes (0 = no cap)")
    p.add_argument("--no-intermediate", action="store_true",
                   help="Skip writing step1..step8 JSON files")
    p.add_argument("--quiet", action="store_true", help="Suppress progress output")


def _add_classify_parser(sub) -> None:
    p = sub.add_parser("classify", help="Classify a trace against an existing taxonomy")
    p.add_argument("--taxonomy", required=True, help="Path to taxonomy.json")
    p.add_argument("--trace", required=True,
                   help="Path to a single trace file (or a JSONL with one trace per line — first one is used)")
    p.add_argument("--model", default=None, help="Override the LLM model")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="adamast",
        description="AdaMAST: generate and use multi-agent failure taxonomies",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_generate_parser(sub)
    _add_classify_parser(sub)

    args = parser.parse_args(argv)

    if args.command == "generate":
        return _cmd_generate(args)
    if args.command == "classify":
        return _cmd_classify(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


def _cmd_generate(args) -> int:
    try:
        taxonomy = generate_taxonomy(
            traces=args.traces,
            output_dir=args.output,
            model=args.model,
            max_codes=args.max_codes,
            save_intermediate=not args.no_intermediate,
            verbose=not args.quiet,
        )
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    counts = taxonomy.get("metadata", {}).get("counts", {})
    if not args.quiet:
        print(f"\nWrote taxonomy with {counts.get('total', '?')} codes to {args.output}/taxonomy.json")
    return 0


def _cmd_classify(args) -> int:
    trace_path = Path(args.trace)
    if not trace_path.exists():
        print(f"ERROR: trace file not found: {trace_path}", file=sys.stderr)
        return 1

    loaded = load_traces(trace_path, verbose=False)
    if not loaded:
        print("ERROR: could not parse a trace from the provided file", file=sys.stderr)
        return 1

    diagnosis = classify_trace(args.taxonomy, loaded[0], model=args.model)
    if diagnosis is None:
        print("ERROR: classification failed", file=sys.stderr)
        return 1

    print(json.dumps(diagnosis.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
