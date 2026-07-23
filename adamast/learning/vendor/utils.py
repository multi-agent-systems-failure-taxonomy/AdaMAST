"""Small shared helpers used across pipeline stages."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def progress(msg: str) -> None:
    """Print a timestamped progress line, normalizing fancy arrows for ASCII logs."""
    msg = msg.replace("→", "->").replace("←", "<-")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def save_json(data: Any, path: Path) -> None:
    """Write ``data`` as pretty-printed UTF-8 JSON and log the path."""
    path = Path(path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(f"Saved: {path}")


def truncate_text(text: str, max_length: int) -> str:
    """Truncate at a sentence boundary near the limit, falling back to ellipsis."""
    if not text or len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_period = truncated.rfind(".")
    if last_period > max_length * 0.7:
        return truncated[: last_period + 1]
    return truncated.rstrip() + "..."


def normalize_code_ids(codes: List[Dict[str, Any]], category: str) -> List[Dict[str, Any]]:
    """Renumber codes sequentially within a category (A.1, A.2, ...).

    The LLM occasionally returns codes in unusual nesting (e.g. with a nested
    ``code`` dict, or an extra ``issues`` field from a fix step). This
    function smooths over those shapes so downstream code can rely on a flat
    schema.
    """
    normalized: List[Dict[str, Any]] = []
    for i, code in enumerate(codes, 1):
        if isinstance(code.get("code"), dict):
            inner = code.get("code", {})
            actual = dict(inner)
            for key, val in code.items():
                if key not in ("code", "issues") and key not in actual:
                    actual[key] = val
        else:
            actual = dict(code)
            actual.pop("issues", None)
        actual["code"] = f"{category}.{i}"
        normalized.append(actual)
    return normalized


def stratified_sample(traces: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """Sample ``n`` traces stratified by source for representative coverage.

    Uses ``metadata.mas_name`` as the source key (this is set by every
    loader in :mod:`adamast.traces`). When there's only one source — or
    fewer traces than ``n`` — we just return everything we have. Otherwise
    every source gets at least 2 samples plus a proportional share of the
    remainder, evenly spaced within each source.
    """
    if len(traces) <= n:
        return list(traces)

    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for trace in traces:
        source = trace.get("metadata", {}).get("mas_name", "unknown")
        by_source.setdefault(source, []).append(trace)

    if len(by_source) <= 1:
        step = max(1, len(traces) // n)
        return [traces[i] for i in range(0, len(traces), step)][:n]

    sources = list(by_source.keys())
    min_per_source = min(2, n // len(sources))
    remaining = n - min_per_source * len(sources)

    sample: List[Dict[str, Any]] = []
    for source in sources:
        source_traces = by_source[source]
        count = min_per_source
        proportion = len(source_traces) / len(traces)
        count += max(0, int(remaining * proportion))
        count = min(count, len(source_traces))

        step = max(1, len(source_traces) // count)
        source_sample = [source_traces[i] for i in range(0, len(source_traces), step)][:count]
        sample.extend(source_sample)

    return sample[:n]


def format_trace_for_prompt(trace: Dict[str, Any], max_length: int = 6000) -> str:
    """Render a trace as a prompt-friendly text block.

    The output has a small ``=== TRACE: <id> ===`` header followed by the
    task description, metadata, and the trajectory itself. Trajectories
    longer than the budget are split: 40% from the beginning and 60% from
    the end, since failures usually manifest in the tail.
    """
    lines: List[str] = []
    problem_id = trace.get("problem_id", "unknown")
    lines.append(f"=== TRACE: {problem_id} ===")

    task = trace.get("task", "")
    if task:
        lines.append(f"[TASK] {task[:400]}")
        lines.append("")

    metadata = trace.get("metadata", {})
    if metadata:
        mas = metadata.get("mas_name", "")
        llm = metadata.get("llm_name", "")
        if mas or llm:
            lines.append(f"[META] MAS: {mas}, LLM: {llm}")
        lines.append("")

    raw_trajectory = trace.get("raw_trajectory", "")
    if not raw_trajectory:
        trace_data = trace.get("trace", {})
        if isinstance(trace_data, dict):
            raw_trajectory = trace_data.get("trajectory", "")
        elif isinstance(trace_data, str):
            raw_trajectory = trace_data

    if raw_trajectory:
        header_len = len("\n".join(lines))
        available = max_length - header_len - 50

        if len(raw_trajectory) <= available:
            lines.append(raw_trajectory)
        else:
            begin_chunk = available * 2 // 5
            end_chunk = available - begin_chunk
            lines.append(raw_trajectory[:begin_chunk])
            lines.append("\n... [TRUNCATED] ...\n")
            lines.append(raw_trajectory[-end_chunk:])

    result = "\n".join(lines)
    if len(result) > max_length:
        result = result[:max_length] + "\n... [truncated]"
    return result


def get_trajectory_text(trace: Dict[str, Any]) -> str:
    """Pull the raw trajectory string out of a trace regardless of schema.

    Looks at: top-level ``raw_trajectory``; ``trace.trajectory`` (dict);
    or ``trace`` itself as a string. Returns ``""`` if nothing is found.
    """
    trajectory = trace.get("raw_trajectory", "")
    if trajectory:
        return trajectory
    trace_data = trace.get("trace", {})
    if isinstance(trace_data, dict):
        return trace_data.get("trajectory", "") or ""
    if isinstance(trace_data, str):
        return trace_data
    return ""
