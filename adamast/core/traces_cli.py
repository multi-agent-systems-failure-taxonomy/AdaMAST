"""Trace inspection, export, and conservative pruning CLI."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .config import add_config_argument, config_value, load_adamast_config
from .traces import DEFAULT_TRACE_ROOT, RetentionPolicy, TraceStore

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def collection_status(
    *,
    trace_root: Path | str = DEFAULT_TRACE_ROOT,
    trace_output: Path | str | None = None,
    policy: RetentionPolicy | None = None,
) -> list[dict]:
    """Return one status row per trace collection."""
    rows: list[dict] = []
    if trace_output is not None:
        pending = Path(trace_output).expanduser().resolve() / "pending"
        rows.append(_status_row("program-pending", pending, policy))
    root = Path(trace_root).expanduser().resolve()
    if root.exists():
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                rows.append(_status_row(child.name, child, policy))
    return rows


def export_traces(
    taxonomy_id: str,
    *,
    trace_root: Path | str = DEFAULT_TRACE_ROOT,
) -> list[dict]:
    """Return canonical trace dictionaries for one taxonomy trace folder."""
    _validate_taxonomy_id(taxonomy_id)
    store = TraceStore(Path(trace_root).expanduser().resolve() / taxonomy_id)
    return [trace.to_dict() for trace in store.iter_traces()]


def prune_traces(
    *,
    trace_root: Path | str = DEFAULT_TRACE_ROOT,
    older_than_days: float,
    taxonomy_id: str | None = None,
    include_pending: Path | str | None = None,
    confirm: bool = False,
    now: float | None = None,
) -> dict:
    """Delete old trace files only when confirm=True.

    Only files named ``trace-*.json`` inside selected trace collections are
    considered. This keeps pruning away from manifests, locks, generated
    artifacts, and arbitrary user files.
    """
    if older_than_days <= 0:
        raise ValueError("older_than_days must be positive")
    current = time.time() if now is None else now
    cutoff = current - older_than_days * 86_400
    selected = _selected_dirs(
        trace_root=Path(trace_root).expanduser().resolve(),
        taxonomy_id=taxonomy_id,
        include_pending=Path(include_pending).expanduser().resolve() if include_pending else None,
    )
    candidates: list[Path] = []
    for directory in selected:
        for path in TraceStore(directory).trace_files():
            try:
                if path.stat().st_mtime < cutoff:
                    candidates.append(path)
            except OSError:
                continue
    deleted = 0
    if confirm:
        for path in candidates:
            try:
                path.unlink()
                deleted += 1
            except FileNotFoundError:
                pass
    return {
        "selected_collections": [str(path) for path in selected],
        "older_than_days": older_than_days,
        "matched": len(candidates),
        "deleted": deleted,
        "dry_run": not confirm,
        "files": [str(path) for path in candidates],
    }


def _status_row(
    name: str,
    path: Path,
    policy: RetentionPolicy | None,
) -> dict:
    report = TraceStore(path, policy=policy).retention_report()
    return {
        "collection": name,
        "path": str(path),
        # asdict() only captures dataclass fields; needs_attention is a
        # property the status renderer relies on.
        "needs_attention": report.needs_attention,
        **asdict(report),
    }


def _selected_dirs(
    *,
    trace_root: Path,
    taxonomy_id: str | None,
    include_pending: Path | None,
) -> list[Path]:
    selected: list[Path] = []
    if taxonomy_id:
        _validate_taxonomy_id(taxonomy_id)
        selected.append(trace_root / taxonomy_id)
    elif trace_root.exists():
        selected.extend(
            child for child in sorted(trace_root.iterdir())
            if child.is_dir() and not child.name.startswith(".")
        )
    if include_pending is not None:
        selected.append(include_pending / "pending")
    return selected


def _validate_taxonomy_id(taxonomy_id: str) -> None:
    if not _SAFE_ID.fullmatch(taxonomy_id):
        raise ValueError(
            "taxonomy_id must be filesystem-safe: letters, numbers, '.', '_' or '-'"
        )


def _render_status(rows: list[dict]) -> str:
    if not rows:
        return "No trace collections found."
    lines = [
        "collection        records files oldest_days attention path",
        "---------------- ------- ----- ----------- --------- ----",
    ]
    for row in rows:
        lines.append(
            f"{row['collection'][:16]:<16} "
            f"{row['total_records']:>7} "
            f"{row['file_count']:>5} "
            f"{row['oldest_file_age_days']:>11.1f} "
            f"{str(row['needs_attention']).lower():<9} "
            f"{row['path']}"
        )
    return "\n".join(lines)


def _write_jsonl(records: Iterable[dict], output: Path | None) -> int:
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True)
        for record in records
    ]
    payload = "\n".join(lines) + ("\n" if lines else "")
    if output is None:
        sys.stdout.write(payload)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    return len(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect, export, and conservatively prune AdaMAST trace files."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="show trace collection sizes and retention warnings")
    add_config_argument(status)
    status.add_argument("--trace-root")
    status.add_argument("--trace-output", help="include a program's pending trace folder")
    status.add_argument("--max-total-records", type=int, default=10_000)
    status.add_argument("--max-age-days", type=int, default=90)
    status.add_argument("--json", action="store_true")

    prune = sub.add_parser("prune", help="delete old trace files; dry-run unless --yes")
    add_config_argument(prune)
    prune.add_argument("--trace-root")
    prune.add_argument("--taxonomy-id")
    prune.add_argument("--trace-output", help="also consider this program's pending traces")
    prune.add_argument("--older-than-days", type=float, required=True)
    prune.add_argument("--yes", action="store_true", help="actually delete matched files")
    prune.add_argument("--json", action="store_true")

    export = sub.add_parser("export", help="write one taxonomy's traces as JSONL")
    add_config_argument(export)
    export.add_argument("--trace-root")
    export.add_argument("--taxonomy-id", required=True)
    export.add_argument("--output", type=Path, help="output JSONL file; defaults to stdout")

    args = parser.parse_args(argv)
    try:
        config = load_adamast_config(args.config)
        if args.command == "status":
            rows = collection_status(
                trace_root=config_value(args, config, "trace_root", DEFAULT_TRACE_ROOT),
                trace_output=config_value(args, config, "trace_output"),
                policy=RetentionPolicy(
                    max_total_records=args.max_total_records,
                    max_age_days=args.max_age_days,
                ),
            )
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                print(_render_status(rows))
            return 0
        if args.command == "prune":
            result = prune_traces(
                trace_root=config_value(args, config, "trace_root", DEFAULT_TRACE_ROOT),
                taxonomy_id=args.taxonomy_id,
                include_pending=config_value(args, config, "trace_output"),
                older_than_days=args.older_than_days,
                confirm=args.yes,
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                verb = "Deleted" if args.yes else "Would delete"
                print(
                    f"{verb} {result['matched'] if not args.yes else result['deleted']} "
                    f"trace file(s). Use --json to inspect exact paths."
                )
            return 0
        if args.command == "export":
            records = export_traces(
                args.taxonomy_id,
                trace_root=config_value(args, config, "trace_root", DEFAULT_TRACE_ROOT),
            )
            count = _write_jsonl(records, args.output)
            if args.output is not None:
                print(f"Exported {count} trace(s) to {args.output}")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
