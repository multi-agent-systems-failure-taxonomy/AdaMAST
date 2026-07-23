"""MANUAL: run the real vendored induction pipeline with configured API credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adamast.learning.generation import candidate_from_adamast
from adamast.llm.learning_calls import outcome_blind_trace
from adamast.learning.vendor import generate_taxonomy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace_json")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-dir", default="manual_adamast_output")
    args = parser.parse_args()

    record = json.loads(Path(args.trace_json).read_text(encoding="utf-8"))
    raw = generate_taxonomy(
        traces=[outcome_blind_trace(record)],
        output_dir=args.output_dir,
        model=args.model,
        save_intermediate=True,
        verbose=True,
    )
    print(json.dumps(candidate_from_adamast(raw), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
