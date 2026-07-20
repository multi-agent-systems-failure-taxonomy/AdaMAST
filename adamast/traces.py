"""Trace normalization and validation for taxonomy-generation strategies.

The stable public input is an AdaMAST trace record. Compatibility importers are
kept here, outside individual generation methods, so every strategy can choose
which source formats it accepts without duplicating parsing code.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class TraceFormatError(ValueError):
    """Raised when a trace source cannot be normalized without guessing."""


@dataclass(frozen=True)
class TraceBundle:
    """Normalized traces plus a report suitable for a run manifest."""

    traces: list[dict[str, Any]]
    files: list[str]
    format_counts: Mapping[str, int]

    def report(self) -> dict[str, Any]:
        empty = sum(not str(item.get("raw_trajectory") or "").strip() for item in self.traces)
        return {
            "trace_count": len(self.traces),
            "files": list(self.files),
            "formats": dict(self.format_counts),
            "empty_trajectories": empty,
        }


def load_traces(source: Path | str) -> list[dict[str, Any]]:
    """Load and normalize every trace under ``source``."""

    return load_trace_bundle(source).traces


def load_trace_bundle(source: Path | str) -> TraceBundle:
    """Normalize a JSON/JSONL file or a directory of those files."""

    root = Path(source).expanduser().resolve()
    if not root.exists():
        raise TraceFormatError(f"trace source does not exist: {root}")

    if root.is_file():
        files = [root]
    else:
        files = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
        )
    if not files:
        raise TraceFormatError(f"no .json or .jsonl trace files found under: {root}")

    normalized: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for path in files:
        for record in _load_file(path):
            source_format = str(
                record.get("metadata", {}).get("source_format") or "adamast"
            )
            counts[source_format] += 1
            normalized.append(record)

    if not normalized:
        raise TraceFormatError(f"no trace records could be loaded from: {root}")

    return TraceBundle(
        traces=normalized,
        files=[str(path) for path in files],
        format_counts=dict(counts),
    )


def write_normalized_jsonl(traces: Iterable[Mapping[str, Any]], output: Path | str) -> Path:
    """Write normalized traces as deterministic UTF-8 JSONL."""

    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for trace in traces:
            handle.write(json.dumps(dict(trace), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return target


def _load_file(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise TraceFormatError(f"could not read {path}: {exc}") from exc
    if not text.strip():
        raise TraceFormatError(f"trace file is empty: {path}")

    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return _load_jsonl(path, text)
    return _load_document(path, document)


def _load_document(path: Path, document: Any) -> list[dict[str, Any]]:
    if isinstance(document, dict) and isinstance(document.get("traces"), list):
        document = document["traces"]

    if isinstance(document, dict):
        return [_normalize_record(document, 0, path)]
    if not isinstance(document, list):
        raise TraceFormatError(f"{path}: top-level JSON must be an object or array")
    if not document:
        raise TraceFormatError(f"{path}: trace array is empty")
    if not all(isinstance(item, dict) for item in document):
        raise TraceFormatError(f"{path}: every trace array item must be an object")

    records = list(document)
    if _is_codex_session(records):
        return [_from_codex_session(records, path)]
    if _is_event_log(records):
        return [_from_event_log(records, path)]
    return [_normalize_record(item, index, path) for index, item in enumerate(records)]


def _load_jsonl(path: Path, text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TraceFormatError(
                f"{path}:{line_number}: invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(item, dict):
            raise TraceFormatError(f"{path}:{line_number}: JSONL record must be an object")
        records.append(item)

    if not records:
        raise TraceFormatError(f"{path}: JSONL file contains no records")
    if _is_codex_session(records):
        return [_from_codex_session(records, path)]
    if _is_event_log(records):
        return [_from_event_log(records, path)]
    return [_normalize_record(item, index, path) for index, item in enumerate(records)]


def _normalize_record(item: Mapping[str, Any], index: int, path: Path) -> dict[str, Any]:
    if _is_tau_bench(item):
        return _from_tau_bench(item, index, path)
    if _is_mad(item):
        return _from_mad(item, index, path)

    metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
    trace_id = (
        item.get("trace_id")
        or item.get("problem_id")
        or item.get("id")
        or f"{path.stem}_{index + 1}"
    )
    task = str(item.get("task") or item.get("prompt") or "")

    source_format = "adamast"
    if "messages" in item:
        raw_trajectory = _format_messages(item.get("messages"))
        source_format = "messages"
    elif isinstance(item.get("trajectory"), list):
        raw_trajectory = _format_messages(item.get("trajectory"))
        source_format = "messages"
    elif "raw_trajectory" in item:
        raw_trajectory = _string_value(item.get("raw_trajectory"))
    elif isinstance(item.get("trajectory"), str):
        raw_trajectory = str(item["trajectory"])
    elif isinstance(item.get("trace"), dict) and "trajectory" in item["trace"]:
        raw_trajectory = _string_value(item["trace"].get("trajectory"))
        source_format = "mad-envelope"
    elif isinstance(item.get("trace"), str):
        raw_trajectory = str(item["trace"])
    else:
        raise TraceFormatError(
            f"{path}: trace {trace_id!r} needs raw_trajectory, trajectory, messages, "
            "or trace.trajectory"
        )

    outcome = item.get("outcome")
    if outcome is not None:
        metadata.setdefault("outcome", outcome)
    metadata["source_format"] = source_format

    return {
        "problem_id": str(trace_id),
        "task": task,
        "raw_trajectory": raw_trajectory,
        "metadata": metadata,
    }


def _format_messages(value: Any) -> str:
    if not isinstance(value, list):
        raise TraceFormatError("messages/trajectory must be an array of message objects")
    parts: list[str] = []
    for index, message in enumerate(value, 1):
        if not isinstance(message, dict):
            raise TraceFormatError(f"message {index} must be an object")
        role = str(message.get("role") or message.get("agent") or "unknown").upper()
        name = str(message.get("name") or "").strip()
        label = f"{role}:{name}" if name else role
        content = _message_content(message.get("content"))
        if content:
            parts.append(f"[{label}]\n{content}")
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") or {}
            call_name = function.get("name") or tool_call.get("name") or "unknown"
            arguments = function.get("arguments") or tool_call.get("arguments") or "{}"
            parts.append(f"[{label} TOOL CALL] {call_name}({arguments})")
    return "\n\n".join(parts)


def _message_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    chunks.append(str(text))
            elif item is not None:
                chunks.append(str(item))
        return "\n".join(chunks)
    return json.dumps(value, ensure_ascii=False)


def _is_tau_bench(item: Mapping[str, Any]) -> bool:
    return (
        isinstance(item.get("traj"), list)
        and "task_id" in item
        and "reward" in item
    )


def _from_tau_bench(
    item: Mapping[str, Any], index: int, path: Path
) -> dict[str, Any]:
    task_id = item.get("task_id", index)
    trial = item.get("trial", 0)
    reward = item.get("reward", 0.0)
    info = item.get("info") if isinstance(item.get("info"), dict) else {}
    task_info = info.get("task") if isinstance(info.get("task"), dict) else {}
    trajectory = _format_messages(item.get("traj"))
    trajectory += (
        f"\n\n[OUTCOME]\n{'SUCCESS' if reward == 1.0 else 'FAILURE'} "
        f"(reward={reward})"
    )
    domain = "airline" if "airline" in path.stem.lower() else "retail" if "retail" in path.stem.lower() else "unknown"
    return {
        "problem_id": f"tau_bench_{domain}_{task_id}_trial{trial}",
        "task": str(task_info.get("instruction") or ""),
        "raw_trajectory": trajectory,
        "traj": list(item.get("traj") or []),
        "reward": reward,
        "_format": "tau_bench",
        "metadata": {
            "source_format": "tau-bench",
            "mas_name": "tau_bench",
            "benchmark_name": domain,
            "task_id": task_id,
            "trial": trial,
        },
    }


def _is_mad(item: Mapping[str, Any]) -> bool:
    trace = item.get("trace")
    return (
        "mas_name" in item
        and isinstance(trace, dict)
        and "trajectory" in trace
    )


def _from_mad(item: Mapping[str, Any], index: int, path: Path) -> dict[str, Any]:
    trace = item.get("trace") or {}
    mas_name = str(item.get("mas_name") or "unknown")
    benchmark = str(item.get("benchmark_name") or "unknown")
    trace_id = item.get("trace_id", index)
    return {
        "problem_id": f"{mas_name}_{benchmark}_{trace_id}",
        "task": str(item.get("task") or ""),
        "raw_trajectory": _string_value(trace.get("trajectory")),
        "_format": "mad",
        "metadata": {
            "source_format": "mad",
            "mas_name": mas_name,
            "llm_name": str(item.get("llm_name") or "unknown"),
            "benchmark_name": benchmark,
            "trace_id": trace_id,
        },
    }


def _is_codex_session(records: Sequence[Mapping[str, Any]]) -> bool:
    known = {"session_meta", "response_item", "turn_context", "event_msg"}
    return bool(records) and any(str(item.get("type")) in known for item in records)


def _from_codex_session(records: Sequence[Mapping[str, Any]], path: Path) -> dict[str, Any]:
    task = ""
    model = "unknown"
    parts: list[str] = []
    for item in records:
        item_type = item.get("type")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if item_type == "session_meta":
            task = str(payload.get("instructions") or task)[:2000]
            model = str(payload.get("model_provider") or model)
        elif item_type == "turn_context":
            model = str(payload.get("model") or model)
        elif item_type == "response_item":
            subtype = payload.get("type")
            if subtype == "message":
                text = _message_content(payload.get("content"))
                if text:
                    parts.append(f"[{str(payload.get('role') or 'unknown').upper()}]\n{text}")
            elif subtype == "function_call":
                parts.append(
                    f"[TOOL CALL] {payload.get('name', 'unknown')}"
                    f"({payload.get('arguments', '{}')})"
                )
            elif subtype == "function_call_output":
                parts.append(f"[TOOL OUTPUT]\n{_string_value(payload.get('output'))}")
    if not parts:
        raise TraceFormatError(f"{path}: Codex session contains no messages or tool events")
    return {
        "problem_id": f"codex_{path.parent.name}_{path.stem}",
        "task": task,
        "raw_trajectory": "\n\n".join(parts),
        "_format": "codex_session",
        "metadata": {
            "source_format": "codex-session",
            "mas_name": "codex_session",
            "llm_name": model,
            "trace_id": path.stem,
        },
    }


def _is_event_log(records: Sequence[Mapping[str, Any]]) -> bool:
    return bool(records) and all("event" in item for item in records)


def _from_event_log(records: Sequence[Mapping[str, Any]], path: Path) -> dict[str, Any]:
    problem_id = path.stem
    task = ""
    parts: list[str] = []
    for event in records:
        event_type = str(event.get("event") or "")
        agent = str(event.get("agent") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event_type == "run_start":
            problem_id = str(event.get("problem_id") or problem_id)
            task = str(event.get("task") or "")
        elif event_type == "prompt_sent":
            if data.get("system"):
                parts.append(f"[{agent or 'agent'} SYSTEM]\n{data['system']}")
            if data.get("user"):
                parts.append(f"[{agent or 'agent'} USER]\n{data['user']}")
        elif event_type in {"response_received", "completion_received"}:
            content = data.get("response") or data.get("content") or ""
            parts.append(f"[{agent or 'agent'} RESPONSE]\n{content}")
        elif event_type == "agent_output":
            content = data.get("output") or data.get("content") or ""
            parts.append(f"[{agent or 'agent'} OUTPUT]\n{content}")
        elif event_type in {"run_end", "run_complete", "final_answer"}:
            content = data.get("answer") or data.get("final_answer") or data.get("result")
            if content:
                parts.append(f"[FINAL ANSWER]\n{content}")
    if not parts:
        raise TraceFormatError(f"{path}: event log contains no supported content events")
    return {
        "problem_id": problem_id,
        "task": task,
        "raw_trajectory": "\n\n".join(parts),
        "events": [dict(item) for item in records],
        "metadata": {
            "source_format": "event-log",
            "mas_name": "event_log",
            "trace_id": problem_id,
        },
    }


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
