"""Format-agnostic behavioral signal extraction.

Runs lightweight regex/structural checks over every trace to surface
system-level anomalies (truncation, looping, refusal, tool errors, etc.).
The aggregate summary feeds the A-code generator alongside the
architectural analysis.

These signals are intentionally *cheap*: they should never need an LLM,
and they should work on raw trace text regardless of which MAS produced
it. When the trace doesn't match any signal, that's also informative —
it means the failure is probably reasoning-level, which lands in C.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

from adamast.learning.vendor.utils import get_trajectory_text, progress


class SignalExtractor:
    """Run cheap structural checks over all traces and aggregate the findings."""

    REFUSAL_PATTERNS = [
        r"i cannot", r"i can't", r"i'm unable", r"i am unable",
        r"beyond my (?:scope|ability|capabilities)",
        r"i don't have (?:enough|sufficient)",
        r"this (?:is|seems) too (?:complex|difficult|hard)",
        r"i (?:need|require) more (?:information|context|data)",
    ]

    ERROR_PATTERNS = [
        r"(?:traceback|exception|error|fatal)\s*[:(]",
        r"(?:segmentation fault|core dumped|stack overflow)",
        r"(?:timeout|timed? ?out|deadline exceeded)",
        r"(?:out of memory|oom|memory error|killed)",
        r"(?:rate limit|too many requests|429|503)",
        r"(?:connection (?:refused|reset|timed? ?out))",
        r"(?:token limit|max.?tokens|context.?length|context.?window)",
    ]

    TOOL_CALL_PATTERNS = [
        r"\[(?:ASSISTANT )?TOOL CALL\]",
        r"\[TOOL RESPONSE",
        r'"tool_calls"\s*:',
        r"Action:\s*\w+\[",
    ]

    TOOL_ERROR_PATTERNS = [
        r"tool (?:call |invocation )?(?:failed|error)",
        r"(?:invalid|unknown|undefined) (?:tool|function|action)",
        r"(?:wrong|incorrect|invalid|malformed) argument",
        r'"error"\s*:\s*"[^"]+?"',
        r"(?:tool|function) not found",
        r"(?:permission|access) denied",
    ]

    TRUNCATION_END_PATTERNS = [
        r"[+\-*/=><]\s*$",
        r"[(\[{]\s*$",
        r"\.\.\.\s*$",
        r"\\\s*$",
        r",\s*$",
    ]

    def __init__(self, *, verbose: bool = True):
        self.verbose = verbose
        self._refusal_re = [re.compile(p, re.IGNORECASE) for p in self.REFUSAL_PATTERNS]
        self._error_re = [re.compile(p, re.IGNORECASE) for p in self.ERROR_PATTERNS]
        self._trunc_re = [re.compile(p) for p in self.TRUNCATION_END_PATTERNS]
        self._tool_call_re = [re.compile(p, re.IGNORECASE) for p in self.TOOL_CALL_PATTERNS]
        self._tool_error_re = [re.compile(p, re.IGNORECASE) for p in self.TOOL_ERROR_PATTERNS]

    def _log(self, msg: str) -> None:
        if self.verbose:
            progress(msg)

    def extract(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Run signal extraction over all traces. Returns an aggregate summary."""
        self._log("  Extracting behavioral signals from all traces...")

        signals: Dict[str, Any] = {
            "total_traces": len(traces),
            "truncated": [],
            "empty_output": [],
            "has_errors": [],
            "has_refusal": [],
            "has_repetition": [],
            "abrupt_ending": [],
            "has_tool_calls": [],
            "has_tool_errors": [],
            "length_stats": {},
            "error_types_seen": [],
            "signal_examples": {},
        }

        lengths: List[int] = []
        error_types: Counter = Counter()

        for i, trace in enumerate(traces):
            trajectory = get_trajectory_text(trace)
            trace_id = trace.get("problem_id", str(i))

            if not trajectory:
                signals["empty_output"].append(trace_id)
                lengths.append(0)
                continue

            traj_len = len(trajectory)
            lengths.append(traj_len)

            if len(trajectory.strip()) < 50:
                signals["empty_output"].append(trace_id)

            if self._check_truncation(trajectory):
                signals["truncated"].append(trace_id)

            errors_found = self._check_errors(trajectory)
            if errors_found:
                signals["has_errors"].append(trace_id)
                for err in errors_found:
                    error_types[err] += 1

            if self._check_refusal(trajectory):
                signals["has_refusal"].append(trace_id)

            if self._check_repetition(trajectory):
                signals["has_repetition"].append(trace_id)

            if self._check_abrupt_ending(trajectory):
                signals["abrupt_ending"].append(trace_id)

            if any(p.search(trajectory) for p in self._tool_call_re):
                signals["has_tool_calls"].append(trace_id)

            if any(p.search(trajectory) for p in self._tool_error_re):
                signals["has_tool_errors"].append(trace_id)

        signals["length_stats"] = self._length_stats(lengths)
        signals["error_types_seen"] = [{"type": t, "count": c} for t, c in error_types.most_common(10)]

        self._log_summary(signals)
        return signals

    def format_for_prompt(self, signals: Dict[str, Any]) -> str:
        """Format an aggregate summary for inclusion in an A-code prompt."""
        total = signals.get("total_traces", 0)
        if total == 0:
            return "No traces analyzed."

        lines = [
            "=== BEHAVIORAL SIGNALS (extracted from ALL traces) ===",
            f"Total traces analyzed: {total}",
            "",
        ]

        rows = [
            ("Empty/no output", "empty_output"),
            ("Truncated (mid-sentence/expression cutoff)", "truncated"),
            ("Contains error/exception signals", "has_errors"),
            ("Agent refusal/abandonment", "has_refusal"),
            ("Repetition/looping detected", "has_repetition"),
            ("Abrupt ending (no clean conclusion)", "abrupt_ending"),
            ("Traces with tool/API calls", "has_tool_calls"),
            ("Traces with tool/API errors", "has_tool_errors"),
        ]

        lines.append("Signal prevalence:")
        for label, key in rows:
            count = len(signals.get(key, []))
            pct = (count * 100) // total if total else 0
            if count > 0:
                lines.append(f"  - {label}: {count}/{total} ({pct}%)")

        error_types = signals.get("error_types_seen", [])
        if error_types:
            lines.append("")
            lines.append("Error types observed:")
            for et in error_types[:5]:
                lines.append(f"  - {et['type']}: {et['count']} traces")

        stats = signals.get("length_stats", {})
        if stats:
            lines.append("")
            lines.append(
                f"Trace length (chars): min={stats.get('min', 0)}, "
                f"median={stats.get('median', 0)}, max={stats.get('max', 0)}, "
                f"p10={stats.get('p10', 0)}, p90={stats.get('p90', 0)}"
            )

        return "\n".join(lines)

    # ───── Per-trace checks ─────

    def _check_truncation(self, text: str) -> bool:
        if not text:
            return False
        lines = text.rstrip().split("\n")
        last_line = ""
        for line in reversed(lines):
            if line.strip():
                last_line = line.strip()
                break
        if not last_line:
            return True
        for pattern in self._trunc_re:
            if pattern.search(last_line):
                return True
        tail = text[-500:]
        opens = tail.count("(") + tail.count("[") + tail.count("{")
        closes = tail.count(")") + tail.count("]") + tail.count("}")
        if opens > closes + 2:
            return True
        return False

    def _check_errors(self, text: str) -> List[str]:
        found: List[str] = []
        for pattern in self._error_re:
            match = pattern.search(text)
            if match:
                found.append(match.group(0).strip().lower())
        return found[:3]

    def _check_refusal(self, text: str) -> bool:
        return any(p.search(text) for p in self._refusal_re)

    def _check_repetition(self, text: str) -> bool:
        if len(text) < 500:
            return False

        chunk_size = 200
        chunks: List[str] = []
        for i in range(0, len(text) - chunk_size, chunk_size):
            chunk = text[i:i + chunk_size].strip()
            if len(chunk) > 50:
                chunks.append(chunk)

        if len(chunks) >= 4:
            chunk_counts = Counter(chunks)
            if any(c >= 3 for c in chunk_counts.values()):
                return True

        sentences = [s.strip() for s in re.split(r"[.!?\n]{1,2}", text) if len(s.strip()) > 30]
        if len(sentences) >= 6:
            sentence_counts = Counter(sentences)
            if any(c >= 3 for c in sentence_counts.values()):
                return True

        return False

    def _check_abrupt_ending(self, text: str) -> bool:
        if not text or len(text) < 100:
            return True
        tail = text[-200:].strip()
        if not tail:
            return True
        last_line = tail.split("\n")[-1].strip()
        if last_line and not re.search(r"[.!?\]})>:=\d]$", last_line):
            if len(last_line) > 10 and not last_line.endswith("==="):
                return True
        return False

    # ───── Aggregation helpers ─────

    def _length_stats(self, lengths: List[int]) -> Dict[str, int]:
        if not lengths:
            return {}
        s = sorted(lengths)
        return {
            "min": s[0],
            "max": s[-1],
            "median": s[len(s) // 2],
            "mean": sum(lengths) // len(lengths),
            "p10": s[len(s) // 10] if len(s) >= 10 else s[0],
            "p90": s[9 * len(s) // 10] if len(s) >= 10 else s[-1],
        }

    def _log_summary(self, signals: Dict[str, Any]) -> None:
        total = signals["total_traces"]
        self._log(f"    Truncated: {len(signals['truncated'])}/{total}")
        self._log(f"    Empty output: {len(signals['empty_output'])}/{total}")
        self._log(f"    Errors detected: {len(signals['has_errors'])}/{total}")
        self._log(f"    Refusal/abandonment: {len(signals['has_refusal'])}/{total}")
        self._log(f"    Repetition/looping: {len(signals['has_repetition'])}/{total}")
        self._log(f"    Abrupt endings: {len(signals['abrupt_ending'])}/{total}")
        if signals["has_tool_calls"]:
            self._log(f"    Tool-calling traces: {len(signals['has_tool_calls'])}/{total}")
            self._log(f"    Tool errors: {len(signals['has_tool_errors'])}/{total}")
