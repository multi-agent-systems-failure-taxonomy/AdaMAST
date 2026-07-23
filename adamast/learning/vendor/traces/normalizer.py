"""Trace normalization: many input shapes -> one unified format.

The pipeline expects traces of the shape::

    {
        "problem_id": str,                 # unique within a run
        "task": str,                       # short task description
        "raw_trajectory": str,             # the full agent/MAS execution text
        "metadata": {                      # free-form, with these reserved keys:
            "mas_name": str,               # system / harness name
            "llm_name": str,               # which LLM produced the trace
            "benchmark_name": str,         # optional benchmark id
            "trace_id": Any,               # original id in source format
            "_format": str,                # auto-detected source format tag
            ...                            # any additional metadata is preserved
        }
    }

This module accepts a variety of input shapes (raw conversations, agent
step lists, tau-bench, codex CLI sessions, event logs, KIRA trajectories,
Forgecode conversations) and emits the unified shape.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Public dataclass (a typed alternative for users who prefer it over dicts)
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class UnifiedTrace:
    """Typed representation of a normalized trace.

    Use ``.to_dict()`` when feeding the pipeline; or pass dicts directly
    if you prefer not to introduce another type. Both work.
    """

    problem_id: str
    task: str = ""
    raw_trajectory: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "task": self.task,
            "raw_trajectory": self.raw_trajectory,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnifiedTrace":
        return cls(
            problem_id=str(data.get("problem_id", "")),
            task=str(data.get("task", "")),
            raw_trajectory=str(data.get("raw_trajectory", "")),
            metadata=dict(data.get("metadata", {}) or {}),
        )


# ──────────────────────────────────────────────────────────────────────────
# Format detection
# ──────────────────────────────────────────────────────────────────────────

def _is_tau_bench(item: Dict[str, Any]) -> bool:
    return (
        isinstance(item, dict)
        and "traj" in item
        and "task_id" in item
        and "reward" in item
        and isinstance(item.get("traj"), list)
    )


def _is_codex_entry(item: Dict[str, Any]) -> bool:
    return isinstance(item, dict) and item.get("type") in (
        "session_meta", "response_item", "turn_context", "event_msg",
    )


# Claude Code's ``claude --output-format stream-json`` emits one JSON object
# per line with a ``type`` from this set. Detection is type-driven so we don't
# accidentally claim non-Claude JSONL streams.
_CLAUDE_STREAM_TYPES = {"system", "assistant", "user", "result"}


def _is_claude_stream_entry(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    t = item.get("type")
    if t not in _CLAUDE_STREAM_TYPES:
        return False
    # ``system`` init lines carry session_id + subtype="init".
    # ``assistant``/``user`` lines carry a ``message`` object.
    # ``result`` is the trailing summary.
    if t == "system":
        return item.get("subtype") == "init" or "session_id" in item
    if t in ("assistant", "user"):
        return isinstance(item.get("message"), dict)
    if t == "result":
        return True
    return False


def _is_event_log_entry(item: Dict[str, Any]) -> bool:
    return isinstance(item, dict) and "event" in item


def _is_already_unified(item: Dict[str, Any]) -> bool:
    return (
        isinstance(item, dict)
        and "raw_trajectory" in item
        and isinstance(item.get("raw_trajectory"), str)
    )


def _is_kira_trajectory(item: Dict[str, Any]) -> bool:
    """A list of step dicts with ``step_id`` and either ``tool_calls`` or ``observation``."""
    if isinstance(item, dict) and "trajectory_steps" in item:
        return True
    if isinstance(item, list) and item and isinstance(item[0], dict):
        first = item[0]
        return "step_id" in first and ("tool_calls" in first or "observation" in first)
    return False


def _is_conversation(item: Dict[str, Any]) -> bool:
    """A dict with a ``messages`` list of role/content dicts."""
    if not isinstance(item, dict):
        return False
    messages = item.get("messages")
    return isinstance(messages, list) and bool(messages) and isinstance(messages[0], dict)


# ──────────────────────────────────────────────────────────────────────────
# Per-format converters
# ──────────────────────────────────────────────────────────────────────────

def _flatten_content(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(str(c) for c in content)
    return str(content or "")


def _convert_tau_bench(item: Dict[str, Any], file_stem: str, index: int) -> UnifiedTrace:
    task_id = item.get("task_id", index)
    trial = item.get("trial", 0)
    reward = item.get("reward", 0.0)
    info = item.get("info", {}) or {}
    task_info = info.get("task", {}) or {}
    traj = item.get("traj", [])

    task = task_info.get("instruction", "")

    file_lower = file_stem.lower()
    if "airline" in file_lower:
        domain = "airline"
    elif "retail" in file_lower:
        domain = "retail"
    else:
        domain = "unknown"

    llm_name = "unknown"
    if "gpt-4o" in file_lower:
        llm_name = "gpt-4o"
    elif "sonnet" in file_lower:
        llm_name = "claude-3.5-sonnet"

    parts: List[str] = []
    for msg in traj:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls") or []
        if role == "system":
            parts.append(f"[SYSTEM]\n{content}")
        elif role == "user":
            parts.append(f"[USER]\n{content}")
        elif role == "assistant":
            if content:
                parts.append(f"[ASSISTANT]\n{content}")
            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                parts.append(f"[ASSISTANT TOOL CALL] {fn.get('name', 'unknown')}({fn.get('arguments', '{}')})")
        elif role == "tool":
            tool_name = msg.get("name", "unknown")
            parts.append(f"[TOOL RESPONSE: {tool_name}]\n{content}")

    parts.append(f"\n=== RESULT: {'SUCCESS' if reward == 1.0 else 'FAILURE'} (reward={reward}) ===")

    return UnifiedTrace(
        problem_id=f"tau_bench_{domain}_{task_id}_trial{trial}",
        task=task[:500] if task else "",
        raw_trajectory="\n\n".join(parts),
        metadata={
            "mas_name": "tau_bench",
            "llm_name": llm_name,
            "benchmark_name": domain,
            "trace_id": f"{task_id}_trial{trial}",
            "reward": reward,
            "trial": trial,
            "task_id": task_id,
            "_format": "tau_bench",
        },
    )


def _convert_claude_stream_session(
    entries: List[Dict[str, Any]],
    file_path: Path,
) -> Optional[UnifiedTrace]:
    """Aggregate one ``claude --output-format stream-json`` session into a UnifiedTrace.

    The stream emits JSON-per-line with these shapes:

    - ``{"type": "system", "subtype": "init", "session_id": ..., "cwd": ..., "model": ...}``
      one per session.
    - ``{"type": "assistant", "message": {"content": [{"type": "text"|"tool_use", ...}]}}``
    - ``{"type": "user", "message": {"content": [{"type": "tool_result", "content": ...}]}}``
    - ``{"type": "result", "subtype": "success"|"error_max_turns"|...,
        "is_error": bool, "result": str, "num_turns": int, "session_id": ...}``

    We flatten the assistant/tool-use/tool-result sequence into a single
    readable trace text, mark the result line as the outcome, and stash
    session metadata (model, cwd, num_turns, is_error) for downstream
    outcome-blind projection to optionally strip.
    """
    session_id = ""
    model_name = "unknown"
    cwd = ""
    first_user_text = ""
    parts: List[str] = []
    result_block: Optional[Dict[str, Any]] = None

    for entry in entries:
        t = entry.get("type")
        if t == "system" and entry.get("subtype") == "init":
            session_id = str(entry.get("session_id") or session_id)
            model_name = str(entry.get("model") or model_name)
            cwd = str(entry.get("cwd") or cwd)
            continue
        if t == "assistant":
            msg = entry.get("message") or {}
            for block in (msg.get("content") or []):
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = str(block.get("text") or "").strip()
                    if text:
                        parts.append(f"[ASSISTANT]\n{text}")
                elif btype == "tool_use":
                    name = block.get("name", "unknown")
                    raw_input = block.get("input")
                    try:
                        input_text = json.dumps(raw_input, ensure_ascii=False)
                    except (TypeError, ValueError):
                        input_text = str(raw_input)
                    if len(input_text) > 800:
                        input_text = input_text[:797] + "..."
                    parts.append(f"[TOOL USE: {name}]\n{input_text}")
            continue
        if t == "user":
            msg = entry.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                if not first_user_text:
                    first_user_text = content
                parts.append(f"[USER]\n{content}")
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "tool_result":
                        out = block.get("content")
                        if isinstance(out, list):
                            out_text = "\n".join(
                                str(b.get("text", "")) for b in out
                                if isinstance(b, dict)
                            )
                        else:
                            out_text = str(out or "")
                        if len(out_text) > 1500:
                            out_text = out_text[:1497] + "..."
                        parts.append(f"[TOOL RESULT]\n{out_text}")
                    elif btype == "text":
                        text = str(block.get("text") or "")
                        if not first_user_text:
                            first_user_text = text
                        parts.append(f"[USER]\n{text}")
            continue
        if t == "result":
            result_block = entry
            continue

    if result_block is not None:
        verdict = "SUCCESS"
        if result_block.get("is_error"):
            verdict = f"ERROR ({result_block.get('subtype', 'unknown')})"
        elif result_block.get("subtype") and result_block.get("subtype") != "success":
            verdict = result_block.get("subtype", "unknown").upper()
        summary = str(result_block.get("result") or "").strip()
        if summary:
            parts.append(f"\n=== RESULT: {verdict} ===\n{summary}")
        else:
            parts.append(f"\n=== RESULT: {verdict} ===")

    if not parts:
        return None

    task_line = first_user_text.strip().splitlines()[0] if first_user_text else ""
    metadata: Dict[str, Any] = {
        "mas_name": "claude_code",
        "llm_name": model_name,
        "_format": "claude_stream_json",
    }
    if session_id:
        metadata["trace_id"] = session_id
        metadata["session_id"] = session_id
    if cwd:
        metadata["cwd"] = cwd
    if result_block is not None:
        if "num_turns" in result_block:
            metadata["num_turns"] = result_block["num_turns"]
        if "is_error" in result_block:
            metadata["outcome"] = "error" if result_block["is_error"] else "success"
        if "subtype" in result_block:
            metadata["result_subtype"] = result_block["subtype"]

    return UnifiedTrace(
        problem_id=(session_id or file_path.stem),
        task=(task_line[:500] if task_line else file_path.stem),
        raw_trajectory="\n\n".join(parts),
        metadata=metadata,
    )


def _convert_codex_session(entries: List[Dict[str, Any]], file_path: Path) -> Optional[UnifiedTrace]:
    task = ""
    llm_name = "unknown"
    parts: List[str] = []
    parent_name = file_path.parent.name.lower()

    for entry in entries:
        entry_type = entry.get("type", "")
        payload = entry.get("payload", {}) or {}

        if entry_type == "session_meta":
            instructions = payload.get("instructions", "")
            task_match = re.search(
                r"\*\*Task\*\*:\s*\n(.*?)(?:\n====|\n---|\n\*\*)",
                instructions, re.DOTALL,
            )
            if task_match:
                task = task_match.group(1).strip()[:500]
            elif instructions:
                task = instructions[:500]
            llm_name = payload.get("model_provider", llm_name)

        elif entry_type == "turn_context":
            model = payload.get("model", "")
            if model:
                llm_name = model

        elif entry_type == "response_item":
            sub_type = payload.get("type", "")
            if sub_type == "message":
                role = payload.get("role", "unknown")
                content_parts = payload.get("content", [])
                if isinstance(content_parts, list):
                    text = "\n".join(
                        p.get("text", "") for p in content_parts
                        if isinstance(p, dict) and p.get("text")
                    )
                elif isinstance(content_parts, str):
                    text = content_parts
                else:
                    text = str(content_parts)
                if text:
                    parts.append(f"[{role.upper()}]\n{text[:2000]}")
            elif sub_type == "function_call":
                fn_name = payload.get("name", "unknown")
                fn_args = payload.get("arguments", "{}")
                if len(fn_args) > 500:
                    fn_args = fn_args[:500] + "..."
                parts.append(f"[TOOL CALL] {fn_name}({fn_args})")
            elif sub_type == "function_call_output":
                output = payload.get("output", "")
                if isinstance(output, str) and len(output) > 1000:
                    output = output[:1000] + "..."
                parts.append(f"[TOOL OUTPUT]\n{output}")

    if not parts:
        return None

    return UnifiedTrace(
        problem_id=f"codex_{parent_name}_{file_path.stem}",
        task=task,
        raw_trajectory="\n\n".join(parts),
        metadata={
            "mas_name": "codex_session",
            "llm_name": llm_name,
            "benchmark_name": "sre",
            "trace_id": file_path.stem,
            "_format": "codex_session",
        },
    )


def _reconstruct_from_events(events: List[Dict[str, Any]], file_stem: str) -> Optional[UnifiedTrace]:
    problem_id = file_stem
    task = ""
    parts: List[str] = []
    agents_seen = set()
    meta: Dict[str, Any] = {}

    for event in events:
        event_type = event.get("event", "")
        agent = event.get("agent", "")
        data = event.get("data", {}) or {}

        if agent:
            agents_seen.add(agent)

        if event_type == "run_start":
            task = event.get("task", "")
            problem_id = event.get("problem_id", file_stem)
            meta["model"] = (event.get("meta") or {}).get("model", "")

        elif event_type == "prompt_sent":
            system_prompt = data.get("system", "")
            user_prompt = data.get("user", "")
            if agent:
                parts.append(f"\n=== {agent.upper()} ===")
            if system_prompt:
                parts.append(f"[System] {system_prompt[:500]}")
            if user_prompt:
                parts.append(f"[User] {user_prompt[:1000]}")

        elif event_type == "response_received":
            response = data.get("response", data.get("content", ""))
            if response:
                parts.append(f"[Response] {response}")

        elif event_type == "agent_output":
            output = data.get("output", data.get("content", str(data)))
            if agent:
                parts.append(f"\n=== {agent.upper()} OUTPUT ===")
            parts.append(str(output)[:2000])

        elif event_type in ("run_end", "run_complete", "final_answer"):
            answer = data.get("answer", data.get("final_answer", data.get("result", "")))
            if answer:
                parts.append(f"\n=== FINAL_ANSWER ===\n{answer}")

    if not parts:
        return None

    return UnifiedTrace(
        problem_id=str(problem_id),
        task=task,
        raw_trajectory="\n".join(parts),
        metadata={
            "mas_name": "event_log",
            "llm_name": meta.get("model", "unknown"),
            "benchmark_name": "unknown",
            "trace_id": problem_id,
            "agents_seen": sorted(agents_seen),
            "_format": "event_log",
        },
    )


def _convert_kira_trajectory(steps: List[Dict[str, Any]], task_id: str,
                              instruction: str = "",
                              task_metadata: Optional[Dict[str, Any]] = None) -> UnifiedTrace:
    metadata = dict(task_metadata or {})
    parts: List[str] = []
    commands: List[str] = []

    for step in steps[:30]:
        step_id = step.get("step_id", "?")

        reasoning = step.get("reasoning_content") or step.get("reasoning") or ""
        if reasoning:
            parts.append(f"[Step {step_id} - Reasoning]\n{str(reasoning)[:300]}")

        tool_calls = step.get("tool_calls") or []
        for tc in tool_calls:
            fn = tc.get("function_name", "")
            args = tc.get("arguments", {}) or {}
            if fn == "execute_commands":
                cmds = args.get("commands", []) or []
                for cmd in cmds:
                    ks = cmd.get("keystrokes", "")
                    commands.append(ks)
                    parts.append(f"[Step {step_id} - Action]\n$ {ks}")
            elif fn == "task_complete":
                parts.append(f"[Step {step_id} - Action]\n[TASK_COMPLETE]")
            else:
                parts.append(f"[Step {step_id} - Action]\n[{fn}]")

        obs = step.get("observation", {}) or {}
        results = obs.get("results") or []
        for r in results:
            content = r.get("content", "")
            if content:
                parts.append(f"[Step {step_id} - Observation]\n{str(content)[:500]}")

    duration = 0.0
    if len(steps) >= 2:
        try:
            t0 = datetime.fromisoformat(steps[0].get("timestamp", ""))
            t1 = datetime.fromisoformat(steps[-1].get("timestamp", ""))
            duration = (t1 - t0).total_seconds()
        except (ValueError, TypeError):
            pass

    metadata.update({
        "mas_name": metadata.get("mas_name", "kira"),
        "llm_name": metadata.get("model_name", metadata.get("llm_name", "unknown")),
        "trace_id": task_id,
        "commands_executed": commands,
        "duration_s": duration,
        "total_steps": len(steps),
        "_format": "kira_trajectory",
    })

    return UnifiedTrace(
        problem_id=task_id,
        task=instruction[:500] if instruction else "",
        raw_trajectory="\n\n".join(parts),
        metadata=metadata,
    )


def _convert_conversation(conversation: Dict[str, Any], task_id: str,
                          instruction: str = "",
                          task_metadata: Optional[Dict[str, Any]] = None) -> UnifiedTrace:
    metadata = dict(task_metadata or {})
    messages = conversation.get("messages", []) or []
    parts: List[str] = []
    commands: List[str] = []
    step_id = 0

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []

        if role == "system":
            parts.append(f"[SYSTEM]\n{content[:500]}")
        elif role == "user":
            parts.append(f"[USER]\n{content[:500]}")
        elif role == "assistant":
            step_id += 1
            if content:
                parts.append(f"[ASSISTANT Step {step_id}]\n{content[:500]}")
            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                fn_name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                if fn_name == "shell":
                    cmd = args.get("command", "")
                    commands.append(cmd)
                    parts.append(f"[Step {step_id} Action] $ {cmd}")
                elif fn_name in ("write", "patch", "multi_patch"):
                    path = args.get("file_path", args.get("path", ""))
                    parts.append(f"[Step {step_id} Action] [{fn_name.upper()}: {path}]")
                elif fn_name == "read":
                    parts.append(f"[Step {step_id} Action] [READ: {args.get('file_path', '')}]")
                elif fn_name:
                    parts.append(f"[Step {step_id} Action] [{fn_name}]")
        elif role == "tool":
            parts.append(f"[TOOL RESPONSE]\n{content[:500]}")

    metadata.update({
        "mas_name": metadata.get("mas_name", "conversation"),
        "llm_name": metadata.get("model_name", metadata.get("llm_name", "unknown")),
        "trace_id": task_id,
        "commands_executed": commands,
        "_format": "conversation",
    })

    return UnifiedTrace(
        problem_id=task_id,
        task=instruction[:500] if instruction else "",
        raw_trajectory="\n\n".join(parts),
        metadata=metadata,
    )


def _convert_generic_trace(item: Dict[str, Any], index: int) -> UnifiedTrace:
    """Convert an item that *looks* like a unified trace but may be missing fields."""
    mas_name = item.get("mas_name", item.get("metadata", {}).get("mas_name", "unknown"))
    llm_name = item.get("llm_name", item.get("metadata", {}).get("llm_name", "unknown"))
    benchmark = item.get("benchmark_name", item.get("metadata", {}).get("benchmark_name", "unknown"))
    trace_id = item.get("trace_id", item.get("metadata", {}).get("trace_id", index))

    trace_data = item.get("trace", {})
    trajectory = ""
    if isinstance(trace_data, dict):
        trajectory = trace_data.get("trajectory", "") or ""
    elif isinstance(trace_data, str):
        trajectory = trace_data
    if not trajectory:
        trajectory = item.get("raw_trajectory", "") or ""

    problem_id = item.get("problem_id") or f"{mas_name}_{benchmark}_{trace_id}"

    task = item.get("task", "")
    if not task:
        task_match = re.search(
            r"task_prompt.*?[:|]\s*(.*?)(?:\n\*\*|\n\n|\n\|)",
            trajectory, re.IGNORECASE | re.DOTALL,
        )
        if task_match:
            task = task_match.group(1).strip()[:500]

    base_meta = dict(item.get("metadata", {}) or {})
    base_meta.update({
        "mas_name": mas_name,
        "llm_name": llm_name,
        "benchmark_name": benchmark,
        "trace_id": trace_id,
        "_format": base_meta.get("_format", "generic"),
    })
    if "mast_annotation" in item:
        base_meta["mast_annotation"] = item["mast_annotation"]

    return UnifiedTrace(
        problem_id=str(problem_id),
        task=task,
        raw_trajectory=trajectory,
        metadata=base_meta,
    )


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def normalize_trace(
    item: Any,
    *,
    problem_id: Optional[str] = None,
    task: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    file_path: Optional[Path] = None,
) -> Optional[UnifiedTrace]:
    """Normalize one trace into the unified shape.

    Accepts: a dict in any supported schema, an already-unified dict, a
    plain string trajectory, or a list of KIRA-style step dicts. Pass
    ``problem_id`` / ``task`` / ``metadata`` to override the auto-detected
    values. Returns ``None`` if the input has no usable trajectory.
    """
    if item is None:
        return None

    if isinstance(item, str):
        if not item.strip():
            return None
        unified = UnifiedTrace(
            problem_id=problem_id or "trace_0",
            task=task or "",
            raw_trajectory=item,
            metadata=dict(metadata or {}),
        )
        unified.metadata.setdefault("_format", "raw_string")
        unified.metadata.setdefault("mas_name", "unknown")
        return unified

    if isinstance(item, list):
        if _is_kira_trajectory(item):
            return _convert_kira_trajectory(
                item, task_id=problem_id or "kira_0",
                instruction=task or "",
                task_metadata=metadata,
            )
        return None

    if not isinstance(item, dict):
        return None

    if _is_tau_bench(item):
        stem = file_path.stem if file_path else "tau_bench"
        return _convert_tau_bench(item, stem, 0)

    if _is_conversation(item):
        return _convert_conversation(
            item, task_id=problem_id or item.get("task_id", "conv_0"),
            instruction=task or item.get("instruction", ""),
            task_metadata=metadata,
        )

    if "trajectory_steps" in item and isinstance(item["trajectory_steps"], list):
        return _convert_kira_trajectory(
            item["trajectory_steps"],
            task_id=problem_id or item.get("task_id", "kira_0"),
            instruction=task or item.get("instruction", ""),
            task_metadata=metadata,
        )

    if _is_already_unified(item) or "trace" in item:
        unified = _convert_generic_trace(item, 0)
        if problem_id:
            unified.problem_id = problem_id
        if task:
            unified.task = task
        if metadata:
            unified.metadata.update(metadata)
        return unified

    return None


def normalize_traces(items: Iterable[Any]) -> List[UnifiedTrace]:
    """Normalize an iterable of items, silently dropping anything unrecognized."""
    out: List[UnifiedTrace] = []
    for i, item in enumerate(items):
        unified = normalize_trace(item)
        if unified is None:
            continue
        if not unified.problem_id:
            unified.problem_id = f"trace_{i}"
        out.append(unified)
    return out


# Internal helpers exposed for the loader (which has more file-path context)
__all_internal__ = {
    "_convert_tau_bench": _convert_tau_bench,
    "_convert_codex_session": _convert_codex_session,
    "_convert_claude_stream_session": _convert_claude_stream_session,
    "_reconstruct_from_events": _reconstruct_from_events,
    "_convert_generic_trace": _convert_generic_trace,
    "_is_tau_bench": _is_tau_bench,
    "_is_codex_entry": _is_codex_entry,
    "_is_claude_stream_entry": _is_claude_stream_entry,
    "_is_event_log_entry": _is_event_log_entry,
}
