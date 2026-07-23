"""Program-health inspection for AdaMAST trace_output directories."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from adamast.core.config import add_config_argument, config_value, load_adamast_config
from adamast.core.evidence import EVIDENCE_FILE
from adamast.core.program import MANIFEST_NAME, ProgramWorkspace
from adamast.core.traces import TraceStore


def program_health(trace_output: Path | str) -> dict[str, Any]:
    root = Path(trace_output).expanduser().resolve()
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_file():
        workspace = ProgramWorkspace(root)
        stale_sessions = workspace.reconcile_stale_sessions()
        manifest = workspace.load()
    else:
        stale_sessions = []
        manifest = {}
    evidence = _read_json(root / EVIDENCE_FILE)
    decisions = _recent_decisions(root)
    generation = manifest.get("generation") or {}
    refinement = manifest.get("refinement") or {}
    usage = manifest.get("usage") or {}
    return {
        "trace_output": str(root),
        "manifest_exists": manifest_path.is_file(),
        "program_id": manifest.get("program_id"),
        "repo": manifest.get("repo"),
        "adamast_model": manifest.get("adamast_model"),
        "active_taxonomy_id": manifest.get("taxonomy_id") or "mast",
        "active_sessions": manifest.get("active_sessions", []),
        "reconciled_stale_sessions": stale_sessions,
        "pending_traces": TraceStore(root / "pending").count(),
        "generation": {
            "state": generation.get("state", "unknown"),
            "last_error": generation.get("last_error"),
            "retry_after_count": generation.get("retry_after_count"),
        },
        "refinement": {
            "state": refinement.get("state", "unknown"),
            "last_error": refinement.get("last_error"),
            "rounds_completed": refinement.get("rounds_completed", 0),
            "traces_since_refinement": refinement.get("traces_since_refinement", 0),
            "trace_refs": len(refinement.get("trace_refs", [])),
        },
        "evidence": {
            "checkpoint_count": len(evidence.get("checkpoints", []))
            if isinstance(evidence, dict)
            else 0,
            "taxonomy_count": len(evidence.get("taxonomies", {}))
            if isinstance(evidence, dict)
            else 0,
        },
        "usage": {
            "totals": usage.get("totals") or {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            },
            "recent_events": (usage.get("events") or [])[-10:],
        },
        "recent_decisions": decisions,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Show AdaMAST program health for one trace_output directory."
    )
    add_config_argument(parser)
    parser.add_argument("trace_output", nargs="?")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_adamast_config(args.config)
        trace_output = args.trace_output or config_value(args, config, "trace_output")
        if not trace_output:
            parser.error("trace_output is required as an argument or in adamast.json")
        health = program_health(trace_output)
        if args.json:
            print(json.dumps(health, indent=2, ensure_ascii=False))
        else:
            print(_render_text(health))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _recent_decisions(root: Path, *, limit: int = 10) -> list[str]:
    lines: list[str] = []
    for name in ("decisions.log", "codex-decisions.log"):
        path = root / name
        try:
            lines.extend(path.read_text(encoding="utf-8").splitlines())
        except OSError:
            continue
    return lines[-limit:]


def _render_text(health: dict[str, Any]) -> str:
    generation = health["generation"]
    refinement = health["refinement"]
    lines = [
        f"trace_output: {health['trace_output']}",
        f"program_id: {health.get('program_id') or '(no manifest)'}",
        f"repo: {health.get('repo') or '(none)'}",
        f"active_taxonomy_id: {health['active_taxonomy_id']}",
        f"active_sessions: {len(health['active_sessions'])}",
        f"pending_traces: {health['pending_traces']}",
        (
            "generation: "
            f"{generation['state']} last_error={generation.get('last_error')!r}"
        ),
        (
            "refinement: "
            f"{refinement['state']} rounds={refinement['rounds_completed']} "
            f"since={refinement['traces_since_refinement']} "
            f"last_error={refinement.get('last_error')!r}"
        ),
        (
            "evidence: "
            f"{health['evidence']['checkpoint_count']} checkpoints across "
            f"{health['evidence']['taxonomy_count']} taxonomy record(s)"
        ),
        (
            "usage: "
            f"calls={health['usage']['totals'].get('calls', 0)} "
            f"input_tokens={health['usage']['totals'].get('input_tokens', 0)} "
            f"output_tokens={health['usage']['totals'].get('output_tokens', 0)} "
            f"cost_usd={health['usage']['totals'].get('cost_usd', 0.0)}"
        ),
    ]
    if health["recent_decisions"]:
        lines.append("recent_decisions:")
        lines.extend(f"  {line}" for line in health["recent_decisions"])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
