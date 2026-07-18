#!/usr/bin/env python3
"""
LLM_Nomos v13.0 - Architectural Risk Analysis + Behavioral Signal Extraction

Pipeline:
  Step 1: SystemDomainAnalyzer (system overview + domain knowledge)
  Step 2: TraceStructureExtractor (trace format + agents + architecture)
  Step 2.5: TraceSignalExtractor (format-agnostic behavioral signals from ALL traces)
  Step 3: CategoryGenerator A (fully generated: architectural risk + empirical behavioral)
  Step 4: CategoryGenerator B (fully generated: role guidance + capabilities)
  Step 5: CategoryGenerator C (domain-seeded + trace-grounded)
  Step 6: CrossCategoryDeduplicator
  Step 7: CrossCategoryValidator
  Step 8: TaxonomyChecker (validate + fix all rules)

Category Definitions:
  A: System failures - agent-independent (can happen to ANY agent)
      No hardcoded base codes. Generated via:
      - Architectural stage: risk analysis from system topology/handoffs (no traces)
      - Empirical stage: behavioral anomaly detection from trace signals + samples
  B: Role-specific failures - about QUALITY of work (Role_*, dynamically discovered)
  C: Domain reasoning failures - MUST NOT contain agent type
      No hardcoded base codes. Generated via:
      - Domain-Seeded stage: from common_error_patterns + terminology error associations
      - Trace-Grounded stage: reasoning flaw detection from actual trace content

Role Discovery:
  Roles are dynamically discovered from trace content via LLM classification.
  Common roles include solver, checker, refiner, coordinator — but any role
  can be discovered (e.g., translator, reasoner, verifier, planner).
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Tuple, Optional, Set
from collections import Counter

from ..providers import TextProvider, create_provider, resolve_model
from ..traces import load_traces


# =============================================================================
# CONFIGURATION
# =============================================================================

class Config:
    PROVIDER = os.getenv("ADAMAST_PROVIDER", "")
    MODEL = os.getenv("ADAMAST_MODEL", "")
    TIMEOUT = 180
    MAX_WORKERS = 8
    TRACES_FOR_ANALYSIS = 20
    TRACES_PER_AGENT = 50
    ENABLE_PARALLEL = True
    MAX_CODES = 0  # 0 = no cap; >0 = cap total codes after dedup


# =============================================================================
# ROLE DEFINITIONS
# =============================================================================

# Default role definitions — used as fallback hints in the LLM prompt.
# The actual roles are discovered dynamically from trace content in Step 2.
DEFAULT_ROLE_DEFINITIONS = {
    "solver": {
        "definition": "Agent that generates solutions, answers, code, or content in response to a problem or task.",
        "key_behavior": "Produces output that attempts to solve/answer the given problem.",
        "purpose": "Generate solutions, code, or outputs"
    },
    "checker": {
        "definition": "Agent that verifies, validates, or evaluates solutions produced by other agents.",
        "key_behavior": "Assesses correctness and provides accept/reject judgment.",
        "purpose": "Verify, review, or test solutions"
    },
    "refiner": {
        "definition": "Agent that improves solutions based on feedback, critique, or failed verification.",
        "key_behavior": "Takes existing output + feedback and produces improved version.",
        "purpose": "Improve solutions based on feedback"
    },
    "coordinator": {
        "definition": "Agent that orchestrates workflow, routes tasks, or makes selection decisions between multiple outputs.",
        "key_behavior": "Controls flow, chooses between options, decides when to terminate.",
        "purpose": "Orchestrate workflow and make decisions"
    }
}


# =============================================================================
# FAILURE CATEGORY PROMPTS (guidance for A-code generation, not hardcoded codes)
# =============================================================================

A_FAILURE_CATEGORIES = """
When generating Category A (System Failure) codes, consider these failure categories.
Not all categories will apply to every system — generate codes only for categories
that are relevant based on the architecture and trace evidence.

1. OUTPUT ISSUES: Agent produces no output, partial output, garbled output, or
   output that cannot be used by downstream agents. Consider: empty responses,
   truncated mid-sentence, malformed structure, output that doesn't match
   expected format for the system.

2. CONTEXT / MEMORY ISSUES: Agent loses track of prior information, contradicts
   its own earlier reasoning, forgets constraints, or cannot process all input
   because it exceeds capacity. Consider: context window overflow, information
   loss across long traces, re-deriving already established facts.

3. INTER-AGENT COMMUNICATION ISSUES: Information is lost, corrupted, or
   misrouted between agents. Consider: handoff failures, information not
   properly passed to next stage, downstream agent missing upstream context,
   miscommunication between agents.

4. BEHAVIORAL ANOMALIES: Agent exhibits pathological behavior patterns.
   Consider: repetitive/looping output, circular reasoning, refusal to engage,
   abandonment mid-task, degrading output quality over the course of the trace.

5. EXECUTION ERRORS: System-level failures during agent execution. Consider:
   timeouts, crashes, API errors, rate limiting, resource exhaustion, runtime
   exceptions visible in the trace.

6. INSTRUCTION COMPLIANCE: Agent fails to follow its system prompt or task
   instructions. Consider: ignoring constraints, responding to a different
   problem than asked, not adhering to output format requirements specified
   in the prompt.

7. TOOL / API INTERACTION ISSUES: Agent fails when invoking external tools,
   APIs, or function calls. Consider: calling wrong tool for the task,
   passing incorrect or malformed arguments, misinterpreting tool response
   data, tool returning errors that agent doesn't handle, agent retrying
   failed tool calls without adjusting, agent ignoring tool results.
   This applies to any system where agents interact with external tools,
   databases, or APIs as part of their workflow.

IMPORTANT GUIDELINES:
- Generate codes that describe CAUSES, not just symptoms. "Token limit caused
  truncation" is better than "output is missing its ending."
- Keep codes format-agnostic — they should apply regardless of specific trace
  delimiters or markers used by this particular system.
- Each code should represent a genuinely distinct failure mode. Do NOT generate
  multiple codes that describe variants of the same underlying problem.
- If a failure mode is plausible based on the architecture but not observed in
  traces, include it and mark evidence as "theoretical".
"""

def build_b_role_guidance(role_details: Dict) -> str:
    """Build dynamic B-code role guidance from discovered roles and their definitions."""
    lines = [
        "When generating Category B (Role-Specific Quality Failure) codes, consider",
        "quality failure categories per role. Not all will apply to every system — generate",
        "codes only for ACTIVE roles and only for failures relevant to the system's architecture",
        "and capabilities.",
        "",
    ]

    for role_name, details in role_details.items():
        if not details.get('agents'):
            continue
        purpose = details.get('purpose', 'Unknown purpose')
        definition = details.get('definition', '')
        lines.append(f"{role_name.upper()} quality failures (purpose: {purpose}):")
        lines.append(f"  Role definition: {definition}")
        lines.append(f"  Consider: What ways can an agent whose job is to '{purpose}' do that job INCORRECTLY?")
        lines.append(f"  Think about: wrong output, poor quality output, missed important aspects,")
        lines.append(f"  inappropriate method/strategy, superficial work, ignoring relevant information.")
        lines.append("")

    lines.extend([
        "CRITICAL RULE — A/B BOUNDARY:",
        "B codes are NEVER about system-level failures. These belong in Category A:",
        "  - Agent produced no output, empty output, or truncated output -> A code",
        "  - Agent timed out, crashed, or hit token limits -> A code",
        "  - Output is malformed or unparseable -> A code",
        "  - Agent refused to engage or abandoned the task -> A code",
        "B codes are ONLY about the agent doing its job INCORRECTLY — it functioned,",
        "produced output, but the QUALITY of that output was wrong.",
    ])

    return "\n".join(lines)

# Keywords that indicate a B code is really an A code (system/output failure, not quality)
B_CODE_A_TYPE_KEYWORDS = [
    "no output", "empty output", "unable to provide", "placeholder",
    "truncated", "incomplete output", "no response", "missing output",
    "format violation", "malformed output", "unable to produce",
    "failed to generate", "timed out", "crashed",
]


def progress(msg: str):
    msg = msg.replace('→', '->').replace('←', '<-')
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def call_llm(client: TextProvider, prompt: str, system: str = "") -> str:
    """Call the configured provider without changing prompt content."""
    try:
        return client.complete(prompt, system=system)
    except Exception as e:
        progress(f"  [!] LLM error: {e}")
        raise


def extract_json(text: str) -> Dict:
    """Extract JSON from LLM response."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except:
        pass

    patterns = [
        r'```json\s*([\s\S]*?)\s*```',
        r'```\s*([\s\S]*?)\s*```',
        r'\{[\s\S]*\}'
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                json_str = match.group(1) if '```' in pattern else match.group()
                return json.loads(json_str)
            except:
                continue

    return {}


def save_json(data: Any, path: Path):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    progress(f"Saved: {path}")


def normalize_code_ids(codes: List[Dict], category: str) -> List[Dict]:
    """Normalize code IDs to simple format: A.1, A.2, B.1, etc."""
    normalized = []
    for i, code in enumerate(codes, 1):
        # Handle formats:
        # 1. Wrapped: {"code": {...}, "issues": [...]}
        # 2. Hybrid: {"code": {"code": "A.1", "name": "...", ...}, "detection_heuristics": [...], ...}
        # 3. Proper: {"code": "A.1", "name": "...", ...}
        if isinstance(code.get('code'), dict):
            # Nested code dict - merge with top-level fields
            inner_code = code.get('code')
            actual_code = inner_code.copy()
            for key, val in code.items():
                if key not in ('code', 'issues') and key not in actual_code:
                    actual_code[key] = val
        else:
            actual_code = code.copy()
            actual_code.pop('issues', None)
        actual_code['code'] = f"{category}.{i}"
        normalized.append(actual_code)
    return normalized


def truncate_text(text: str, max_length: int) -> str:
    """Truncate text at sentence boundary."""
    if not text or len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_period = truncated.rfind('.')
    if last_period > max_length * 0.7:
        return truncated[:last_period + 1]
    return truncated.rstrip() + "..."


def stratified_sample(traces: List[Dict], n: int) -> List[Dict]:
    """Sample n traces stratified by source (mas_name) for representative coverage."""
    if len(traces) <= n:
        return traces

    # Group traces by source
    by_source = {}
    for trace in traces:
        source = trace.get('metadata', {}).get('mas_name', 'unknown')
        by_source.setdefault(source, []).append(trace)

    if len(by_source) <= 1:
        # Single source — just take evenly spaced samples
        step = max(1, len(traces) // n)
        return [traces[i] for i in range(0, len(traces), step)][:n]

    # Allocate proportionally but ensure at least 2 per source
    sources = list(by_source.keys())
    min_per_source = min(2, n // len(sources))
    remaining = n - min_per_source * len(sources)

    sample = []
    for source in sources:
        source_traces = by_source[source]
        # Base allocation
        count = min_per_source
        # Proportional bonus from remaining
        proportion = len(source_traces) / len(traces)
        count += max(0, int(remaining * proportion))
        count = min(count, len(source_traces))

        # Evenly spaced within each source
        step = max(1, len(source_traces) // count)
        source_sample = [source_traces[i] for i in range(0, len(source_traces), step)][:count]
        sample.extend(source_sample)

    return sample[:n]


def format_trace(trace: Dict, max_length: int = 6000) -> str:
    """Format trace for prompt inclusion."""
    lines = []
    problem_id = trace.get('problem_id', 'unknown')
    lines.append(f"=== TRACE: {problem_id} ===")

    task = trace.get('task', '')
    if task:
        lines.append(f"[TASK] {task[:400]}")
        lines.append("")

    metadata = trace.get('metadata', {})
    if metadata:
        mas = metadata.get('mas_name', '')
        llm = metadata.get('llm_name', '')
        if mas or llm:
            lines.append(f"[META] MAS: {mas}, LLM: {llm}")
        lines.append("")

    raw_trajectory = trace.get('raw_trajectory', '')
    if not raw_trajectory:
        trace_data = trace.get('trace', {})
        if isinstance(trace_data, dict):
            raw_trajectory = trace_data.get('trajectory', '')

    if raw_trajectory:
        header_len = len('\n'.join(lines))
        available = max_length - header_len - 50

        if len(raw_trajectory) <= available:
            lines.append(raw_trajectory)
        else:
            # Give more space to the end of the trace where failures often manifest
            begin_chunk = available * 2 // 5  # 40% to beginning
            end_chunk = available - begin_chunk  # 60% to end
            lines.append(raw_trajectory[:begin_chunk])
            lines.append("\n... [TRUNCATED] ...\n")
            lines.append(raw_trajectory[-end_chunk:])

    result = "\n".join(lines)
    if len(result) > max_length:
        result = result[:max_length] + "\n... [truncated]"
    return result


# =============================================================================
# TRACE SIGNAL EXTRACTOR (Pre-analysis for A-code generation)
# =============================================================================

class TraceSignalExtractor:
    """
    Format-agnostic behavioral signal extraction from ALL traces.

    Runs lightweight checks over every trace to detect system-level anomalies
    before A-code generation. Produces an aggregate summary that the A-code
    generator uses alongside architectural context.

    Signals detected:
    - Truncation patterns (mid-sentence cutoffs, incomplete expressions)
    - Repetition/looping (repeated text blocks within a trace)
    - Empty or near-empty outputs
    - Error/exception signals
    - Abrupt endings vs clean completions
    - Refusal/abandonment language
    - Output length distribution (for spotting outliers)
    """

    # Refusal / abandonment phrases (case-insensitive)
    REFUSAL_PATTERNS = [
        r"i cannot", r"i can't", r"i'm unable", r"i am unable",
        r"beyond my (?:scope|ability|capabilities)",
        r"i don't have (?:enough|sufficient)",
        r"this (?:is|seems) too (?:complex|difficult|hard)",
        r"i (?:need|require) more (?:information|context|data)",
    ]

    # Error / exception signals
    ERROR_PATTERNS = [
        r"(?:traceback|exception|error|fatal)\s*[:(]",
        r"(?:segmentation fault|core dumped|stack overflow)",
        r"(?:timeout|timed? ?out|deadline exceeded)",
        r"(?:out of memory|oom|memory error|killed)",
        r"(?:rate limit|too many requests|429|503)",
        r"(?:connection (?:refused|reset|timed? ?out))",
        r"(?:token limit|max.?tokens|context.?length|context.?window)",
    ]

    # Tool interaction patterns
    TOOL_CALL_PATTERNS = [
        r'\[(?:ASSISTANT )?TOOL CALL\]',
        r'\[TOOL RESPONSE',
        r'"tool_calls"\s*:',
        r'Action:\s*\w+\[',
    ]

    TOOL_ERROR_PATTERNS = [
        r'tool (?:call |invocation )?(?:failed|error)',
        r'(?:invalid|unknown|undefined) (?:tool|function|action)',
        r'(?:wrong|incorrect|invalid|malformed) argument',
        r'"error"\s*:\s*"[^"]+?"',
        r'(?:tool|function) not found',
        r'(?:permission|access) denied',
    ]

    # Truncation end signals (line ends with these = likely truncated)
    TRUNCATION_END_PATTERNS = [
        r"[+\-*/=><]\s*$",       # ends with operator
        r"[(\[{]\s*$",           # ends with opening delimiter
        r"\.\.\.\s*$",           # ends with ellipsis
        r"\\\s*$",               # ends with backslash (line continuation)
        r",\s*$",                # ends with comma (incomplete list)
    ]

    def __init__(self):
        self._refusal_re = [re.compile(p, re.IGNORECASE) for p in self.REFUSAL_PATTERNS]
        self._error_re = [re.compile(p, re.IGNORECASE) for p in self.ERROR_PATTERNS]
        self._trunc_re = [re.compile(p) for p in self.TRUNCATION_END_PATTERNS]
        self._tool_call_re = [re.compile(p, re.IGNORECASE) for p in self.TOOL_CALL_PATTERNS]
        self._tool_error_re = [re.compile(p, re.IGNORECASE) for p in self.TOOL_ERROR_PATTERNS]

    def extract_signals(self, traces: List[Dict]) -> Dict:
        """Run signal extraction over all traces. Returns aggregate summary."""
        progress("  Extracting behavioral signals from all traces...")

        signals = {
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

        lengths = []
        error_types = Counter()

        for i, trace in enumerate(traces):
            trajectory = self._get_trajectory(trace)
            trace_id = trace.get('problem_id', str(i))

            if not trajectory:
                signals["empty_output"].append(trace_id)
                lengths.append(0)
                continue

            traj_len = len(trajectory)
            lengths.append(traj_len)

            # Check for empty/near-empty
            stripped = trajectory.strip()
            if len(stripped) < 50:
                signals["empty_output"].append(trace_id)

            # Check for truncation
            if self._check_truncation(trajectory):
                signals["truncated"].append(trace_id)

            # Check for errors
            errors_found = self._check_errors(trajectory)
            if errors_found:
                signals["has_errors"].append(trace_id)
                for err in errors_found:
                    error_types[err] += 1

            # Check for refusal
            if self._check_refusal(trajectory):
                signals["has_refusal"].append(trace_id)

            # Check for repetition/looping
            if self._check_repetition(trajectory):
                signals["has_repetition"].append(trace_id)

            # Check for abrupt ending
            if self._check_abrupt_ending(trajectory):
                signals["abrupt_ending"].append(trace_id)

            # Check for tool call usage
            if any(p.search(trajectory) for p in self._tool_call_re):
                signals["has_tool_calls"].append(trace_id)

            # Check for tool errors
            if any(p.search(trajectory) for p in self._tool_error_re):
                signals["has_tool_errors"].append(trace_id)

        # Compute length stats
        if lengths:
            sorted_lengths = sorted(lengths)
            signals["length_stats"] = {
                "min": sorted_lengths[0],
                "max": sorted_lengths[-1],
                "median": sorted_lengths[len(sorted_lengths) // 2],
                "mean": sum(lengths) // len(lengths),
                "p10": sorted_lengths[len(sorted_lengths) // 10] if len(sorted_lengths) >= 10 else sorted_lengths[0],
                "p90": sorted_lengths[9 * len(sorted_lengths) // 10] if len(sorted_lengths) >= 10 else sorted_lengths[-1],
            }

        # Top error types
        signals["error_types_seen"] = [{"type": t, "count": c} for t, c in error_types.most_common(10)]

        # Log summary
        progress(f"    Truncated: {len(signals['truncated'])}/{len(traces)}")
        progress(f"    Empty output: {len(signals['empty_output'])}/{len(traces)}")
        progress(f"    Errors detected: {len(signals['has_errors'])}/{len(traces)}")
        progress(f"    Refusal/abandonment: {len(signals['has_refusal'])}/{len(traces)}")
        progress(f"    Repetition/looping: {len(signals['has_repetition'])}/{len(traces)}")
        progress(f"    Abrupt endings: {len(signals['abrupt_ending'])}/{len(traces)}")
        if signals["has_tool_calls"]:
            progress(f"    Tool-calling traces: {len(signals['has_tool_calls'])}/{len(traces)}")
            progress(f"    Tool errors: {len(signals['has_tool_errors'])}/{len(traces)}")

        return signals

    def format_for_prompt(self, signals: Dict) -> str:
        """Format signal summary for inclusion in A-code generation prompt."""
        total = signals["total_traces"]
        if total == 0:
            return "No traces analyzed."

        lines = ["=== BEHAVIORAL SIGNALS (extracted from ALL traces) ==="]
        lines.append(f"Total traces analyzed: {total}")
        lines.append("")

        # Prevalence summary
        categories = [
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
        for label, key in categories:
            count = len(signals.get(key, []))
            pct = (count * 100) // total if total > 0 else 0
            if count > 0:
                lines.append(f"  - {label}: {count}/{total} ({pct}%)")

        # Error types
        error_types = signals.get("error_types_seen", [])
        if error_types:
            lines.append("")
            lines.append("Error types observed:")
            for et in error_types[:5]:
                lines.append(f"  - {et['type']}: {et['count']} traces")

        # Length stats
        stats = signals.get("length_stats", {})
        if stats:
            lines.append("")
            lines.append(f"Trace length (chars): min={stats.get('min', 0)}, "
                         f"median={stats.get('median', 0)}, max={stats.get('max', 0)}, "
                         f"p10={stats.get('p10', 0)}, p90={stats.get('p90', 0)}")

        return "\n".join(lines)

    def _get_trajectory(self, trace: Dict) -> str:
        """Get raw trajectory text from any trace format."""
        trajectory = trace.get('raw_trajectory', '')
        if not trajectory:
            trace_data = trace.get('trace', {})
            if isinstance(trace_data, dict):
                trajectory = trace_data.get('trajectory', '')
            elif isinstance(trace_data, str):
                trajectory = trace_data
        return trajectory

    def _check_truncation(self, text: str) -> bool:
        """Check if trace appears truncated."""
        if not text:
            return False

        # Check last non-empty line for truncation signals
        lines = text.rstrip().split('\n')
        last_line = ''
        for line in reversed(lines):
            if line.strip():
                last_line = line.strip()
                break

        if not last_line:
            return True

        for pattern in self._trunc_re:
            if pattern.search(last_line):
                return True

        # Check for unclosed delimiters in last 500 chars
        tail = text[-500:]
        opens = tail.count('(') + tail.count('[') + tail.count('{')
        closes = tail.count(')') + tail.count(']') + tail.count('}')
        if opens > closes + 2:
            return True

        return False

    def _check_errors(self, text: str) -> List[str]:
        """Check for error/exception signals. Returns list of error types found."""
        found = []
        for pattern in self._error_re:
            if pattern.search(text):
                # Extract the category name from the pattern
                match = pattern.search(text)
                if match:
                    found.append(match.group(0).strip().lower())
        return found[:3]  # Limit per trace

    def _check_refusal(self, text: str) -> bool:
        """Check for refusal/abandonment language."""
        for pattern in self._refusal_re:
            if pattern.search(text):
                return True
        return False

    def _check_repetition(self, text: str) -> bool:
        """Check for repetition/looping patterns."""
        if len(text) < 500:
            return False

        # Split into chunks and check for repeated blocks
        chunk_size = 200
        chunks = []
        for i in range(0, len(text) - chunk_size, chunk_size):
            chunk = text[i:i + chunk_size].strip()
            if len(chunk) > 50:
                chunks.append(chunk)

        if len(chunks) < 4:
            return False

        # Check if any chunk appears 3+ times
        chunk_counts = Counter(chunks)
        for count in chunk_counts.values():
            if count >= 3:
                return True

        # Also check for repeated sentences (more fine-grained)
        sentences = re.split(r'[.!?\n]{1,2}', text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 30]
        if len(sentences) >= 6:
            sentence_counts = Counter(sentences)
            for count in sentence_counts.values():
                if count >= 3:
                    return True

        return False

    def _check_abrupt_ending(self, text: str) -> bool:
        """Check if trace ends abruptly without a clean conclusion."""
        if not text or len(text) < 100:
            return True

        # Get last 200 chars
        tail = text[-200:].strip()
        if not tail:
            return True

        last_line = tail.split('\n')[-1].strip()

        # Ends mid-word (no ending punctuation and doesn't look complete)
        if last_line and not re.search(r'[.!?\]})>:=\d]$', last_line):
            # Allow lines that look like they end naturally
            if len(last_line) > 10 and not last_line.endswith('==='):
                return True

        return False


# =============================================================================
# STEP 1: SYSTEM DOMAIN ANALYZER
# =============================================================================

class SystemDomainAnalyzer:
    """
    Step 1: Analyze traces to understand:
    - What domain/task type (math, code, etc.)
    - What makes tasks hard
    - Domain-specific terminology
    - Common error patterns in this domain
    """

    def __init__(self, client: TextProvider):
        self.client = client

    def analyze(self, traces: List[Dict]) -> Dict:
        progress("Step 1: System Domain Analyzer")

        sample = stratified_sample(traces, Config.TRACES_FOR_ANALYSIS)
        traces_text = "\n\n".join([format_trace(t, max_length=3000) for t in sample])

        # Extract metadata hints from traces (mas_name, benchmark, task samples)
        meta_hints = set()
        task_samples = []
        for t in traces[:20]:
            meta = t.get('metadata', {})
            for key in ('mas_name', 'benchmark_name', 'llm_name'):
                val = meta.get(key, '')
                if val and val != 'unknown':
                    meta_hints.add(f"{key}: {val}")
            task_text = t.get('task', '')
            if task_text and len(task_samples) < 3:
                task_samples.append(task_text[:200])
        hints_block = ""
        if meta_hints or task_samples:
            hints_block = "\nMETADATA HINTS:\n" + "\n".join(sorted(meta_hints))
            if task_samples:
                hints_block += "\n\nSAMPLE TASK DESCRIPTIONS:\n" + "\n---\n".join(task_samples)
            hints_block += "\n"

        prompt = f"""Analyze these system traces to understand the DOMAIN and TASK TYPE.
{hints_block}
TRACES:
{traces_text}

Extract:
1. What domain is this? (math, code repair, incident response, etc.)
2. What makes tasks difficult in this domain?
3. Key terminology used in this domain
4. Common error patterns you observe

OUTPUT JSON:
{{
  "domain": {{
    "name": "e.g., Mathematics, Code Repair, Incident Response",
    "content_type": "e.g., proofs, numerical answers, code patches",
    "task_complexity": "What makes tasks hard in this domain"
  }},
  "subdomains": ["algebra", "geometry", "combinatorics"],
  "domain_terminology": [
    {{
      "term": "permutation",
      "meaning": "Ordered arrangement of elements",
      "error_associations": ["confused with combination"]
    }}
  ],
  "common_error_patterns": [
    {{
      "name": "off_by_one",
      "description": "Counting n items but getting n-1 or n+1",
      "detection_hints": ["fence post", "inclusive vs exclusive"]
    }}
  ],
  "correctness_criteria": [
    {{
      "criterion": "numerical_accuracy",
      "description": "Final number must be exactly correct",
      "how_to_verify": "Compare with ground truth"
    }}
  ]
}}"""

        response = call_llm(self.client, prompt)
        result = extract_json(response)

        progress(f"  Domain: {result.get('domain', {}).get('name', 'Unknown')}")
        progress(f"  Subdomains: {result.get('subdomains', [])}")
        progress(f"  Error patterns: {len(result.get('common_error_patterns', []))}")

        return result


# =============================================================================
# STEP 2: TRACE STRUCTURE EXTRACTOR
# =============================================================================

class TraceStructureExtractor:
    """
    Step 2: Extract from traces:
    - Actual agent names (extracted via regex from raw trajectories)
    - Agent role classification (dynamically discovered via LLM)
    - Trace format patterns
    - System architecture

    Roles are classified by LLM based on actual agent behavior in traces,
    not pattern matching against agent names. This allows any MAS with
    any agent naming convention to be properly classified.
    """

    def __init__(self, client: TextProvider):
        self.client = client

    def _extract_agents_from_trajectories(self, traces: List[Dict]) -> Set[str]:
        """Extract actual agent names from traces using multiple methods."""
        agents = set()

        # HIGH-PRECISION patterns - these are reliable agent indicators
        high_precision_patterns = [
            # ChatDev style: | **assistant_role_name** | Agent Name |
            r'\|\s*\*\*(?:assistant_role_name|user_role_name)\*\*\s*\|\s*([^|]+?)\s*\|',
            # AG2/HyperAgent style: Agent_Name
            r'\b(Agent_[A-Za-z_]+(?:__[A-Za-z_]+)?)\b',
            # Event log style: === AGENT_NAME === (any capitalized word between triple-equals)
            r'===\s*([A-Z][A-Z_]+)\s*===',
            # Bracket style: [agent_name (role)] or [agent_name]
            r'\[([A-Za-z][A-Za-z0-9_]+)\s*(?:\([^)]*\))?\]',
        ]

        # KNOWN AGENT TITLES - only match these specific role suffixes
        known_titles = [
            'Officer', 'Programmer', 'Engineer', 'Reviewer', 'Tester',
            'Designer', 'Counselor', 'Architect', 'Manager', 'Verifier'
        ]
        title_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:' + '|'.join(known_titles) + r'))\b'

        # Track how many traces each agent appears in (for frequency filtering)
        from collections import Counter
        agent_trace_count = Counter()
        n_traces = len(traces)

        for trace in traces:
            metadata = trace.get('metadata', {})

            # tau_bench: single-agent tool-calling system
            if metadata.get('_format') == 'tau_bench':
                domain = metadata.get('benchmark_name', 'unknown')
                agent_name = f"{domain.title()} Agent"
                agents.add(agent_name)
                agent_trace_count[agent_name.lower().strip()] += 1
                continue

            # Codex session: single-agent tool-calling system
            if metadata.get('_format') == 'codex_session':
                agents.add("SRE Agent")
                agent_trace_count["sre agent"] += 1
                continue

            # Agents found in THIS trace
            trace_agents = set()

            # Method 1: Check metadata for agents_seen (from event log reconstruction)
            agents_seen = metadata.get('agents_seen', [])
            if agents_seen:
                for agent in agents_seen:
                    if isinstance(agent, str) and len(agent) > 1:
                        agents.add(agent.strip())
                        trace_agents.add(agent.strip().lower())

            # Method 2: Extract from raw trajectory text
            trajectory = trace.get('raw_trajectory', '')
            if not trajectory:
                trace_data = trace.get('trace', {})
                if isinstance(trace_data, dict):
                    trajectory = trace_data.get('trajectory', '')

            if trajectory:
                # Apply high-precision patterns
                non_agent_markers = {'TRACE', 'ERROR', 'TASK', 'META', 'SYSTEM', 'OUTPUT', 'INPUT', 'RESULT', 'END', 'START'}
                for pattern in high_precision_patterns:
                    matches = re.findall(pattern, trajectory, re.IGNORECASE)
                    for match in matches:
                        agent_name = match.strip()
                        if len(agent_name) > 1 and len(agent_name) < 50 and agent_name.upper() not in non_agent_markers:
                            agents.add(agent_name)
                            trace_agents.add(agent_name.lower())

                # Apply known title pattern
                matches = re.findall(title_pattern, trajectory)
                for match in matches:
                    agent_name = match.strip()
                    if len(agent_name) > 3 and len(agent_name) < 50:
                        agents.add(agent_name)
                        trace_agents.add(agent_name.lower())

            for ta in trace_agents:
                agent_trace_count[ta] += 1

        # Filter out agents appearing in <10% of traces (rare evolved variants)
        if n_traces >= 10:
            threshold = max(2, n_traces * 0.10)
            rare = {name for name, count in agent_trace_count.items() if count < threshold}
            if rare:
                agents = {a for a in agents if a.lower().strip() not in rare}
                progress(f"  Filtered {len(rare)} rare agents (appear in <10% of traces): {sorted(rare)[:5]}")

        # Filter out math/code variable false positives
        # Patterns: single uppercase + underscore (F_k, P_n), very short (ci, ab),
        # or all-lowercase <=2 chars that aren't plausible agent names
        _var_pattern = re.compile(
            r'^[A-Z]_[a-z0-9]$'           # F_k, P_n, S_i
            r'|^[a-z]{1,2}$'              # ci, ab, x
            r'|^[A-Z][a-z]?$'             # F, Pk
            r'|^\d'                        # starts with digit
            r'|^(?:true|false|null|none)$' # keywords
        , re.IGNORECASE)
        agents = {a for a in agents if not _var_pattern.match(a)}

        # Normalize: deduplicate case variants (SOLVER, Solver, solver -> solver)
        normalized = {}
        for agent in agents:
            key = agent.lower().strip()
            # Keep the more readable form (title case or original if multi-word)
            if key not in normalized or agent[0].isupper():
                normalized[key] = agent
        # Use lowercase keys as canonical names for role-word agents,
        # keep original casing for multi-word names (e.g., "Chief Technology Officer")
        result = set()
        for key, original in normalized.items():
            if ' ' in original or '_' in original:
                result.add(original)  # multi-word: keep original casing
            else:
                result.add(key)  # single-word: lowercase canonical
        return result

    def _classify_agents_via_llm(self, agents: Set[str], traces: List[Dict]) -> Tuple[Dict, Dict]:
        """Classify agents into roles using LLM based on their actual trace behavior.

        Returns (agent_to_role, role_details) where roles are dynamically discovered.
        """
        if not agents:
            return {}, {}

        # Extract a sample of each agent's behavior from traces
        agent_samples = {}
        for agent in agents:
            agent_lower = agent.lower()
            samples = []
            for trace in traces[:50]:  # Check first 50 traces
                trajectory = trace.get('raw_trajectory', '')
                if not trajectory:
                    trace_data = trace.get('trace', {})
                    if isinstance(trace_data, dict):
                        trajectory = trace_data.get('trajectory', '')
                if not trajectory:
                    continue

                # Check if this agent appears in the trace
                if agent_lower in trajectory.lower():
                    # Extract a snippet around the agent's name (up to 500 chars)
                    idx = trajectory.lower().find(agent_lower)
                    start = max(0, idx - 100)
                    end = min(len(trajectory), idx + 400)
                    snippet = trajectory[start:end]
                    samples.append(snippet)
                    if len(samples) >= 3:
                        break

            if samples:
                agent_samples[agent] = "\n---\n".join(samples)
            else:
                agent_samples[agent] = "(no trace samples found)"

        # Build the LLM prompt
        agents_with_samples = "\n\n".join(
            f"AGENT: {agent}\nBEHAVIOR SAMPLES:\n{sample[:800]}"
            for agent, sample in agent_samples.items()
        )

        # Build canonical role descriptions
        canonical_roles = "\n".join(
            f"  - {role}: {info['definition']}"
            for role, info in DEFAULT_ROLE_DEFINITIONS.items()
        )

        prompt = f"""Classify these agents into functional roles based on what they DO in the traces.

AGENTS AND THEIR BEHAVIOR:
{agents_with_samples}

CANONICAL ROLES (you MUST assign each agent to exactly one of these four roles):
{canonical_roles}

Every agent must map to one of: solver, checker, refiner, coordinator.
Do NOT invent new role names. Choose the closest canonical role based on the
agent's ACTUAL BEHAVIOR in the traces, not just its name.

Guidelines:
- An agent that produces answers, code, analysis, or translations is a "solver"
- An agent that reviews, verifies, validates, or judges output is a "checker"
- An agent that fixes, debugs, or improves based on feedback is a "refiner"
- An agent that routes, orchestrates, or selects between options is a "coordinator"

OUTPUT JSON:
{{
  "agent_roles": {{
    "AgentName": {{
      "role": "solver|checker|refiner|coordinator",
      "definition": "What this agent does based on trace evidence",
      "purpose": "Short purpose phrase (5-10 words)"
    }}
  }}
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)
            agent_roles = result.get('agent_roles', {})
        except Exception as e:
            progress(f"  [!] LLM role classification failed: {e}")
            agent_roles = {}

        # Build agent_to_role and role_details from LLM response
        canonical = set(DEFAULT_ROLE_DEFINITIONS.keys())
        agent_to_role = {}
        role_details = {}

        # Name-based heuristic as fallback when LLM returns no info for an agent
        def _heuristic_role(name: str) -> str:
            n = name.lower()
            if any(w in n for w in ('verif', 'check', 'review', 'valid', 'test', 'judge')):
                return 'checker'
            if any(w in n for w in ('refin', 'debug', 'fix', 'improv', 'repair')):
                return 'refiner'
            if any(w in n for w in ('coordinat', 'orchestrat', 'route', 'manag', 'dispatch')):
                return 'coordinator'
            return 'solver'

        for agent in agents:
            info = agent_roles.get(agent, {})
            role = info.get('role', '')
            if not role:
                role = _heuristic_role(agent)
            # Snap non-canonical roles to closest canonical bin
            if role not in canonical:
                role_lower = role.lower()
                if any(w in role_lower for w in ('check', 'verif', 'valid', 'review', 'test', 'judge')):
                    role = 'checker'
                elif any(w in role_lower for w in ('refin', 'debug', 'fix', 'improv', 'repair')):
                    role = 'refiner'
                elif any(w in role_lower for w in ('coordinat', 'orchestrat', 'route', 'manag', 'select')):
                    role = 'coordinator'
                else:
                    role = 'solver'
                progress(f"    Snapped non-canonical role '{info.get('role')}' -> '{role}' for {agent}")
            definition = info.get('definition', '')
            purpose = info.get('purpose', '')

            agent_to_role[agent] = role

            if role not in role_details:
                role_details[role] = {
                    "agents": [],
                    "definition": definition,
                    "purpose": purpose,
                }
            role_details[role]["agents"].append(agent)
            # Use the first definition/purpose we see for each role
            if not role_details[role].get("definition") and definition:
                role_details[role]["definition"] = definition
            if not role_details[role].get("purpose") and purpose:
                role_details[role]["purpose"] = purpose

        # Ensure every role has a purpose and definition
        for role, details in role_details.items():
            if not details.get("purpose"):
                # Fall back to default if available
                default = DEFAULT_ROLE_DEFINITIONS.get(role, {})
                details["purpose"] = default.get("purpose", f"Perform {role} tasks")
            if not details.get("definition"):
                default = DEFAULT_ROLE_DEFINITIONS.get(role, {})
                details["definition"] = default.get("definition", f"Agent that performs {role} functions.")

        return agent_to_role, role_details

    def _detect_capabilities(self, traces: List[Dict]) -> Dict:
        """Detect agent capabilities from trace content (tool-calling, code execution, etc.)."""
        capabilities = {
            "has_tool_calling": False,
            "has_code_execution": False,
            "has_web_browsing": False,
            "tool_names_seen": [],
            "interaction_style": "direct_reasoning",  # default
        }

        tool_names = set()

        # Tool-calling patterns across formats
        tool_call_patterns = [
            re.compile(r'\[(?:ASSISTANT )?TOOL CALL\]', re.IGNORECASE),
            re.compile(r'\[TOOL RESPONSE', re.IGNORECASE),
            re.compile(r'"tool_calls"\s*:', re.IGNORECASE),
            re.compile(r'"function"\s*:\s*\{', re.IGNORECASE),
            re.compile(r'Action:\s*\w+\[', re.IGNORECASE),  # ReAct style
            re.compile(r'<tool_call>', re.IGNORECASE),
        ]

        # Code execution patterns
        code_exec_patterns = [
            re.compile(r'```(?:python|bash|javascript|shell)', re.IGNORECASE),
            re.compile(r'exec(?:ute)?_code|run_code|code_interpreter', re.IGNORECASE),
            re.compile(r'IPython\.display|subprocess\.run', re.IGNORECASE),
        ]

        # Web/browsing patterns
        web_patterns = [
            re.compile(r'browse|web_search|search_google|fetch_url', re.IGNORECASE),
        ]

        # Tool name extraction patterns
        tool_name_patterns = [
            re.compile(r'\[(?:ASSISTANT )?TOOL CALL\]\s*(\w+)\(', re.IGNORECASE),
            re.compile(r'"name"\s*:\s*"(\w+)"', re.IGNORECASE),
            re.compile(r'Action:\s*(\w+)\[', re.IGNORECASE),
        ]

        # Sample traces for efficiency
        sample = traces[:min(100, len(traces))]
        tool_call_count = 0

        for trace in sample:
            text = trace.get('raw_trajectory', '')
            if not text:
                continue

            for p in tool_call_patterns:
                if p.search(text):
                    tool_call_count += 1
                    capabilities["has_tool_calling"] = True
                    break

            for p in code_exec_patterns:
                if p.search(text):
                    capabilities["has_code_execution"] = True
                    break

            for p in web_patterns:
                if p.search(text):
                    capabilities["has_web_browsing"] = True
                    break

            # Extract tool names
            for p in tool_name_patterns:
                matches = p.findall(text)
                for m in matches:
                    if len(m) > 2 and len(m) < 50 and m.lower() not in ('unknown', 'none', 'null'):
                        tool_names.add(m)

        capabilities["tool_names_seen"] = sorted(list(tool_names))[:30]

        # Determine primary interaction style
        if capabilities["has_tool_calling"] and tool_call_count > len(sample) * 0.3:
            capabilities["interaction_style"] = "tool_calling"
        elif capabilities["has_code_execution"]:
            capabilities["interaction_style"] = "code_execution"
        elif capabilities["has_tool_calling"]:
            capabilities["interaction_style"] = "mixed"  # some tool calling but not dominant

        return capabilities

    def _build_fallback_architecture(self, role_details: Dict, capabilities: Dict) -> Dict:
        """Construct architecture from discovered roles when LLM fails.

        Uses role purposes to classify roles into functional categories
        (content-producing, validating, orchestrating) rather than exact names.
        """
        active_roles = [r for r, d in role_details.items() if d.get('agents')]

        # Classify roles by function based on their purpose/definition
        orchestrating_roles = []
        validating_roles = []
        producing_roles = []
        for role in active_roles:
            purpose = role_details.get(role, {}).get('purpose', '').lower()
            definition = role_details.get(role, {}).get('definition', '').lower()
            text = f"{purpose} {definition} {role}"
            if any(w in text for w in ['orchestrat', 'coordinat', 'route', 'select', 'manage', 'direct']):
                orchestrating_roles.append(role)
            elif any(w in text for w in ['verif', 'validat', 'check', 'review', 'test', 'evaluat']):
                validating_roles.append(role)
            else:
                producing_roles.append(role)

        # Infer topology from role types
        if len(active_roles) <= 1:
            topology = "single-agent"
        elif orchestrating_roles:
            topology = "hierarchical"
        elif len(active_roles) >= 2:
            topology = "sequential"
        else:
            topology = "sequential"

        # Infer verification pattern
        if validating_roles:
            verification = "dedicated-checker"
        else:
            verification = "none"

        # Build handoffs from role flow
        handoffs = []
        if producing_roles and validating_roles:
            handoffs.append({
                "from_agent": producing_roles[0],
                "to_agent": validating_roles[0],
                "what_is_passed": "output for validation",
                "failure_risk": "Output context lost or misinterpreted"
            })
        if orchestrating_roles and producing_roles:
            handoffs.append({
                "from_agent": orchestrating_roles[0],
                "to_agent": producing_roles[0],
                "what_is_passed": "task routing/selection",
                "failure_risk": "Wrong routing or selection decision"
            })

        # Termination owner
        if orchestrating_roles:
            termination = f"{orchestrating_roles[0]} (orchestrator)"
        elif validating_roles:
            termination = f"{validating_roles[0]} (accept/reject terminates)"
        elif producing_roles:
            termination = f"{producing_roles[0]} (single pass)"
        else:
            termination = "unknown"

        return {
            "topology": topology,
            "topology_details": f"Inferred from {len(active_roles)} active roles: {', '.join(active_roles)}",
            "verification_pattern": verification,
            "verification_details": f"{'Dedicated validation agents verify outputs' if verification != 'none' else 'No dedicated verification agent found'}",
            "termination_owner": termination,
            "critical_handoffs": handoffs
        }

    def _build_fallback_trace_format(self, sample_traces: List[Dict]) -> Dict:
        """Construct trace format from trace content when LLM fails."""
        markers = set()
        for trace in sample_traces:
            text = trace.get('raw_trajectory', '')
            # Detect common markers
            for m in re.findall(r'===\s*(\w+)\s*===', text):
                markers.add(f"=== {m} ===")
            for m in re.findall(r'\[(\w+)\]', text[:500]):
                if m.upper() in ('SYSTEM', 'USER', 'ASSISTANT', 'TOOL'):
                    markers.add(f"[{m}]")

        return {
            "agent_markers": list(markers)[:10],
            "key_fields": [],
            "output_structure": "Raw text trajectory with agent markers",
            "example_patterns": list(markers)[:5]
        }

    def extract(self, traces: List[Dict]) -> Dict:
        progress("Step 2: Trace Structure Extractor")

        # Step 2a: Pre-extract agents using regex (more reliable than LLM for this)
        progress("  Extracting agents from trajectories...")
        extracted_agents = self._extract_agents_from_trajectories(traces)
        progress(f"  Found {len(extracted_agents)} unique agents via pattern matching")

        # Step 2b: Classify agents into roles using LLM (based on trace behavior)
        progress("  Classifying agent roles via LLM...")
        agent_to_role, role_details = self._classify_agents_via_llm(extracted_agents, traces)

        # Log role distribution
        for role, details in role_details.items():
            if details["agents"]:
                progress(f"    {role}: {len(details['agents'])} agents - {details['agents'][:3]}")

        # Step 2b.5: Detect agent capabilities (tool-calling, code execution, etc.)
        progress("  Detecting agent capabilities...")
        capabilities = self._detect_capabilities(traces)
        progress(f"    Interaction style: {capabilities['interaction_style']}")
        if capabilities["tool_names_seen"]:
            progress(f"    Tools seen: {capabilities['tool_names_seen'][:10]}")

        # Step 2c: Use LLM to analyze trace format and architecture
        sample = stratified_sample(traces, Config.TRACES_FOR_ANALYSIS)
        traces_text = "\n\n".join([format_trace(t, max_length=4000) for t in sample])

        # Provide pre-extracted agents to LLM for context
        agents_list = list(extracted_agents)[:30]  # Limit for prompt size

        # Build role definitions from discovered roles
        role_defs_text = "\n".join(
            f"- {role}: {details.get('definition', 'N/A')}"
            for role, details in role_details.items()
            if details.get('agents')
        )

        prompt = f"""Analyze these system traces to extract TRACE FORMAT and ARCHITECTURE.

PRE-EXTRACTED AGENTS (found via pattern matching):
{json.dumps(agents_list, indent=2)}

AGENT ROLE CLASSIFICATION (LLM-assigned based on trace behavior):
{json.dumps(agent_to_role, indent=2)}

DISCOVERED ROLE DEFINITIONS:
{role_defs_text}

SAMPLE TRACES:
{traces_text}

Analyze the ACTUAL trace content (not our wrapper format). Look for:
1. How agents communicate (markers, formatting)
2. Key fields in the ACTUAL trace (not [TASK], [META] - those are our wrapper)
3. Architecture patterns

OUTPUT JSON:
{{
  "trace_format": {{
    "agent_markers": ["Actual patterns used to mark agents in traces"],
    "key_fields": [
      {{
        "field_name": "actual_field_name",
        "description": "What this field contains",
        "location": "Where to find it in the trace"
      }}
    ],
    "output_structure": "How traces are actually structured",
    "example_patterns": [
      "Regex or text pattern to find important content"
    ]
  }},
  "architecture": {{
    "topology": "sequential | parallel | hierarchical | debate | hybrid",
    "topology_details": "How agents actually interact based on trace evidence",
    "verification_pattern": "self-verify | peer-verify | dedicated-checker | consensus | none",
    "verification_details": "Who verifies what (based on observed agent interactions)",
    "termination_owner": "Who decides when workflow ends",
    "critical_handoffs": [
      {{
        "from_agent": "ActualAgentName",
        "to_agent": "ActualAgentName",
        "what_is_passed": "solution/feedback/verdict",
        "failure_risk": "What can go wrong"
      }}
    ]
  }},
  "agent_role_corrections": {{
    "AgentName": "corrected_role if my auto-classification was wrong"
  }}
}}"""

        response = call_llm(self.client, prompt)
        llm_result = extract_json(response)

        # Apply any role corrections from LLM, snapping to canonical roles
        canonical = set(DEFAULT_ROLE_DEFINITIONS.keys())

        def _snap_to_canonical(raw_role: str) -> str:
            """Snap a free-text role correction to the closest canonical role."""
            if raw_role in canonical:
                return raw_role
            rl = raw_role.lower()
            if any(w in rl for w in ('check', 'verif', 'valid', 'review', 'test', 'judge')):
                return 'checker'
            if any(w in rl for w in ('refin', 'debug', 'fix', 'improv', 'repair')):
                return 'refiner'
            if any(w in rl for w in ('coordinat', 'orchestrat', 'route', 'manag', 'dispatch', 'select')):
                return 'coordinator'
            if any(w in rl for w in ('solv', 'reason', 'generat', 'translat', 'reform', 'preprocess', 'cod', 'analyz', 'answer')):
                return 'solver'
            # "not observed" / unknown → drop this agent entirely
            if any(w in rl for w in ('not observed', 'unused', 'no evidence', 'unknown')):
                return '__drop__'
            return 'solver'  # safe default

        corrections = llm_result.get('agent_role_corrections', {})
        for agent, corrected_role_raw in corrections.items():
            if agent not in agent_to_role:
                continue
            corrected_role = _snap_to_canonical(str(corrected_role_raw))
            if corrected_role == '__drop__':
                # Remove agent entirely — not a real agent
                old_role = agent_to_role.pop(agent, None)
                if old_role in role_details and agent in role_details[old_role].get("agents", []):
                    role_details[old_role]["agents"].remove(agent)
                extracted_agents.discard(agent)
                progress(f"    Dropping {agent}: '{corrected_role_raw}' -> not a real agent")
                continue
            old_role = agent_to_role[agent]
            if old_role != corrected_role:
                progress(f"    Correcting {agent}: {old_role} -> {corrected_role} (from: '{corrected_role_raw}')")
                if old_role in role_details and agent in role_details[old_role].get("agents", []):
                    role_details[old_role]["agents"].remove(agent)
                if corrected_role not in role_details:
                    role_details[corrected_role] = {
                        "agents": [],
                        "definition": DEFAULT_ROLE_DEFINITIONS.get(corrected_role, {}).get('definition', ''),
                        "purpose": DEFAULT_ROLE_DEFINITIONS.get(corrected_role, {}).get('purpose', ''),
                    }
                role_details[corrected_role]["agents"].append(agent)
                agent_to_role[agent] = corrected_role

        # Clean up empty role buckets and any non-canonical roles that slipped through
        for role_name in list(role_details.keys()):
            if role_name not in canonical:
                # Move agents to their closest canonical role
                for a in role_details[role_name].get("agents", []):
                    snapped = _snap_to_canonical(role_name)
                    if snapped == '__drop__':
                        agent_to_role.pop(a, None)
                        extracted_agents.discard(a)
                    else:
                        agent_to_role[a] = snapped
                        if snapped not in role_details:
                            role_details[snapped] = {
                                "agents": [],
                                "definition": DEFAULT_ROLE_DEFINITIONS.get(snapped, {}).get('definition', ''),
                                "purpose": DEFAULT_ROLE_DEFINITIONS.get(snapped, {}).get('purpose', ''),
                            }
                        role_details[snapped]["agents"].append(a)
                del role_details[role_name]
            elif not role_details[role_name].get("agents"):
                del role_details[role_name]

        # Build final result with fallback architecture if LLM returned empty
        llm_arch = llm_result.get('architecture', {})
        if not llm_arch or not llm_arch.get('topology'):
            progress("  [!] LLM returned empty architecture — constructing from discovered agents")
            llm_arch = self._build_fallback_architecture(role_details, capabilities)

        llm_format = llm_result.get('trace_format', {})
        if not llm_format or not llm_format.get('key_fields'):
            progress("  [!] LLM returned empty trace_format — constructing from trace content")
            llm_format = self._build_fallback_trace_format(traces[:5])

        result = {
            "trace_format": llm_format,
            "architecture": llm_arch,
            "discovered_agents": {
                "agents": list(extracted_agents),
                "agent_to_role": agent_to_role,
                "role_details": role_details
            },
            "capabilities": capabilities
        }

        # Log summary
        trace_format = result.get('trace_format', {})
        arch = result.get('architecture', {})

        progress(f"  Trace format: {len(trace_format.get('key_fields', []))} key fields")
        progress(f"  Topology: {arch.get('topology', 'Unknown')}")
        progress(f"  Verification: {arch.get('verification_pattern', 'Unknown')}")

        return result


# =============================================================================
# CATEGORY GENERATOR (Steps 3-5)
# =============================================================================

class CategoryGenerator:
    """
    Generate codes for a category using two-stage approach:

    For A: No base codes - fully generated, guided by failure category prompts
      - Stage 1 (Architectural): Risk analysis based on architecture (no traces)
      - Stage 2 (Empirical): Behavioral anomaly detection from traces + signals

    For B: Start with universal base codes, then extend via two agents
      - Stage 1 (Theoretical): Extend based on architecture and agent interactions
      - Stage 2 (Empirical): Extend based on actual trace content analysis

    For C: No base codes - fully emergent from domain
      - Stage 1 (Theoretical): Generate from domain description
      - Stage 2 (Empirical): Generate from actual trace analysis
    """

    def __init__(self, client: TextProvider, category: str,
                 domain_info: Dict, structure_info: Dict,
                 trace_signals: Dict = None):
        self.client = client
        self.category = category
        self.domain_info = domain_info or {}
        self.structure_info = structure_info or {}
        self.trace_signals = trace_signals or {}

    def _get_active_roles(self) -> List[str]:
        """Get roles that actually have agents discovered in the traces."""
        agents = self.structure_info.get('discovered_agents', {})
        role_details = agents.get('role_details', {})

        active_roles = []
        for role, details in role_details.items():
            if details.get('agents', []):
                active_roles.append(role)

        return active_roles

    def _fallback_discover_roles(self, traces: List[Dict]) -> List[str]:
        """Fallback role discovery from traces when structure_info is empty.

        Uses metadata.agents_seen + name-based heuristics to build
        discovered_agents in structure_info so the B-code prompt can use them.
        """
        agent_names = set()
        for t in traces:
            for a in t.get('metadata', {}).get('agents_seen', []):
                if isinstance(a, str) and len(a) > 1:
                    agent_names.add(a.strip())

        if not agent_names:
            # Try regex on trajectories
            bracket_pat = re.compile(r'\[([A-Za-z][A-Za-z0-9_]+)\s*(?:\([^)]*\))?\]')
            skip = {'TRACE', 'ERROR', 'TASK', 'META', 'SYSTEM', 'OUTPUT', 'INPUT', 'RESULT', 'END', 'START'}
            for t in traces[:30]:
                traj = t.get('raw_trajectory', '')
                for m in bracket_pat.findall(traj):
                    if m.upper() not in skip and len(m) < 50:
                        agent_names.add(m.lower())

        if not agent_names:
            return []

        # Name-based role heuristic
        def _role(n):
            nl = n.lower()
            if any(w in nl for w in ('verif', 'check', 'review', 'valid', 'test', 'judge')):
                return 'checker'
            if any(w in nl for w in ('refin', 'debug', 'fix', 'improv', 'repair')):
                return 'refiner'
            if any(w in nl for w in ('coordinat', 'orchestrat', 'route', 'manag', 'dispatch')):
                return 'coordinator'
            return 'solver'

        agent_to_role = {}
        role_details = {}
        for agent in agent_names:
            role = _role(agent)
            agent_to_role[agent] = role
            if role not in role_details:
                role_details[role] = {
                    "agents": [],
                    "definition": DEFAULT_ROLE_DEFINITIONS.get(role, {}).get('definition', ''),
                    "purpose": DEFAULT_ROLE_DEFINITIONS.get(role, {}).get('purpose', ''),
                }
            role_details[role]["agents"].append(agent)

        # Inject into structure_info so the B-code prompt can use it
        if 'discovered_agents' not in self.structure_info:
            self.structure_info['discovered_agents'] = {}
        self.structure_info['discovered_agents']['agents'] = list(agent_names)
        self.structure_info['discovered_agents']['agent_to_role'] = agent_to_role
        self.structure_info['discovered_agents']['role_details'] = role_details

        progress(f"    Fallback: {len(agent_names)} agents -> {list(role_details.keys())}")
        return list(role_details.keys())

    def _get_base_codes(self) -> List[Dict]:
        """Get universal base codes for this category. All categories are fully generated."""
        return []  # All categories fully generated, guided by category-specific prompts

    def _get_actual_trace_fields(self) -> List[str]:
        """Get the actual field names discovered in traces."""
        trace_format = self.structure_info.get('trace_format', {})
        key_fields = trace_format.get('key_fields', [])
        return [f.get('field_name', f.get('field', '')) for f in key_fields if f.get('field_name') or f.get('field')]

    def generate(self, traces: List[Dict], existing_codes: Dict = None) -> List[Dict]:
        progress(f"\nStep {3 + ['A', 'B', 'C'].index(self.category)}: Category {self.category} Generator")

        # Get base codes
        base_codes = self._get_base_codes()
        if base_codes:
            progress(f"  Universal base codes: {len(base_codes)}")
        else:
            progress(f"  No base codes — fully generated from analysis")

        # For B-codes, show which roles have agents
        if self.category == "B":
            active_roles = self._get_active_roles()
            progress(f"  Active roles with agents: {active_roles}")
            if not active_roles:
                # Fallback: try to discover roles from trace content directly
                progress("  WARNING: No agents in structure_info — attempting trace-based fallback")
                active_roles = self._fallback_discover_roles(traces)
                if not active_roles:
                    progress("  WARNING: No agents discovered for any role - B codes will be empty")
                    return []
                progress(f"  Fallback found roles: {active_roles}")

        # Split traces for two-stage generation (stratified by source)
        mid = len(traces) // 2
        traces_stage1 = stratified_sample(traces[:mid] if mid > 0 else traces, Config.TRACES_PER_AGENT)
        traces_stage2 = stratified_sample(traces[mid:] if mid > 0 else traces, Config.TRACES_PER_AGENT)

        # Stage naming and trace handling per category
        if self.category == "A":
            stage1_name, stage2_name = "Architectural", "Empirical"
        elif self.category == "C":
            stage1_name, stage2_name = "Domain-Seeded", "Trace-Grounded"
        else:
            stage1_name, stage2_name = "Theoretical", "Empirical"

        # Run two stages in parallel
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_stage1 = executor.submit(
                self._run_stage,
                [] if self.category == "A" else traces_stage1,
                stage1_name, base_codes, existing_codes
            )
            future_stage2 = executor.submit(
                self._run_stage, traces_stage2, stage2_name, base_codes, existing_codes
            )

            codes_stage1 = future_stage1.result()
            codes_stage2 = future_stage2.result()

        progress(f"  {stage1_name} stage: {len(codes_stage1)} codes")
        progress(f"  {stage2_name} stage: {len(codes_stage2)} codes")

        # Merge and deduplicate
        merged = self._merge_codes(base_codes, codes_stage1, codes_stage2)
        progress(f"  Merged total: {len(merged)} codes")

        # Post-generation sanitization for A codes: reject role-specific codes
        if self.category == "A":
            merged = self._sanitize_a_codes(merged)

        return merged

    def _sanitize_a_codes(self, codes: List[Dict]) -> List[Dict]:
        """Remove A codes that are inherently role-specific (swap test).

        An A code should describe a failure that could happen to ANY agent.
        If the code's name or definition implies it only applies to one role's
        purpose (e.g., 'verdict', 'refinement', 'coordination'), it's really a
        B code and should not be in category A.
        """
        # Build role-specific concept indicators dynamically from discovered roles
        agents_info = self.structure_info.get('discovered_agents', {})
        role_details = agents_info.get('role_details', {})

        role_concept_indicators = {}
        for role, details in role_details.items():
            indicators = [role]  # The role name itself
            # Add agent names as indicators
            for agent in details.get('agents', []):
                agent_lower = agent.lower()
                if len(agent_lower) > 2:
                    indicators.append(agent_lower)
            # Add purpose-derived keywords
            purpose = details.get('purpose', '').lower()
            definition = details.get('definition', '').lower()
            for word in purpose.split():
                if len(word) > 4:
                    indicators.append(word)
            role_concept_indicators[role] = indicators

        sanitized = []
        removed = []
        for code in codes:
            name_lower = code.get("name", "").lower()
            def_lower = code.get("definition", "").lower()
            text = f"{name_lower} {def_lower}"

            is_role_specific = False
            for role, indicators in role_concept_indicators.items():
                matches = [ind for ind in indicators if ind in text]
                if len(matches) >= 1:
                    # Check if the match is in the code NAME (strong signal) or just definition
                    name_matches = [ind for ind in indicators if ind in name_lower]
                    if name_matches:
                        is_role_specific = True
                        removed.append((code.get("code", ""), code.get("name", ""), role))
                        break

            if not is_role_specific:
                sanitized.append(code)

        if removed:
            progress(f"  A-code sanitization: removed {len(removed)} role-specific codes:")
            for code_id, name, role in removed:
                progress(f"    {code_id} '{name}' → role-specific to {role}")

        return sanitized

    def _get_trace_format_context(self) -> str:
        """Build context about trace format for heuristics."""
        trace_format = self.structure_info.get('trace_format', {})

        lines = ["\n=== TRACE FORMAT CONTEXT ==="]

        # Agent markers
        markers = trace_format.get('agent_markers', [])
        if markers:
            lines.append(f"Agent markers in traces: {markers}")

        # Key fields
        key_fields = trace_format.get('key_fields', [])
        if key_fields:
            lines.append("\nDiscovered fields:")
            for field in key_fields:
                field_name = field.get('field_name', field.get('field', '?'))
                desc = field.get('description', '')
                lines.append(f"  * {field_name}: {desc}")
        else:
            lines.append("\nUse general trace content patterns for heuristics.")

        return '\n'.join(lines)

    def _get_architecture_context(self) -> str:
        """Build full architecture context for A-code generation."""
        arch = self.structure_info.get('architecture', {})
        agents = self.structure_info.get('discovered_agents', {})

        lines = ["\n=== SYSTEM ARCHITECTURE ==="]

        # Topology
        topology = arch.get('topology', 'Unknown')
        topology_details = arch.get('topology_details', '')
        lines.append(f"Topology: {topology}")
        if topology_details:
            lines.append(f"Details: {topology_details}")

        # Verification pattern
        verification = arch.get('verification_pattern', 'Unknown')
        verification_details = arch.get('verification_details', '')
        lines.append(f"\nVerification: {verification}")
        if verification_details:
            lines.append(f"Details: {verification_details}")

        # Termination
        termination = arch.get('termination_owner', 'Unknown')
        lines.append(f"\nTermination owner: {termination}")

        # Critical handoffs
        handoffs = arch.get('critical_handoffs', [])
        if handoffs:
            lines.append("\nCritical handoffs:")
            for h in handoffs:
                from_a = h.get('from_agent', '?')
                to_a = h.get('to_agent', '?')
                passed = h.get('what_is_passed', '?')
                risk = h.get('failure_risk', '?')
                lines.append(f"  {from_a} -> {to_a}: passes {passed} (risk: {risk})")

        # Agents and roles
        role_details = agents.get('role_details', {})
        if role_details:
            lines.append("\n=== AGENTS & ROLES ===")
            for role, details in role_details.items():
                agent_list = details.get('agents', [])
                if agent_list:
                    purpose = details.get('purpose', '')
                    shown = agent_list[:5]
                    more = f" (+{len(agent_list)-5} more)" if len(agent_list) > 5 else ""
                    purpose_str = f" ({purpose})" if purpose else ""
                    lines.append(f"{role.upper()}{purpose_str}: {', '.join(shown)}{more}")

        return '\n'.join(lines)

    def _get_agent_context(self) -> str:
        """Build context about discovered agents."""
        agents = self.structure_info.get('discovered_agents', {})
        if not agents:
            return ""

        lines = ["\n=== DISCOVERED AGENTS ==="]
        role_details = agents.get('role_details', {})

        for role, details in role_details.items():
            agent_list = details.get('agents', [])
            if agent_list:
                purpose = details.get('purpose', '')
                shown = agent_list[:5]
                more = f" (+{len(agent_list)-5} more)" if len(agent_list) > 5 else ""
                purpose_str = f" ({purpose})" if purpose else ""
                lines.append(f"{role.upper()}{purpose_str}: {', '.join(shown)}{more}")

        return '\n'.join(lines)

    def _get_domain_context(self) -> str:
        """Build domain context for C codes."""
        if not self.domain_info:
            return ""

        lines = ["\n=== DOMAIN KNOWLEDGE ==="]
        lines.append(f"Domain: {self.domain_info.get('domain', {}).get('name', 'Unknown')}")

        subdomains = self.domain_info.get('subdomains', [])
        if subdomains:
            lines.append(f"Subdomains: {', '.join(subdomains[:5])}")

        patterns = self.domain_info.get('common_error_patterns', [])
        if patterns:
            lines.append("Common error patterns:")
            for p in patterns[:5]:
                lines.append(f"  - {p.get('name', '')}: {p.get('description', '')}")

        return '\n'.join(lines)

    def _get_signal_context(self) -> str:
        """Build behavioral signal context for A-code empirical stage."""
        if not self.trace_signals:
            return ""
        extractor = TraceSignalExtractor()
        return extractor.format_for_prompt(self.trace_signals)

    def _get_lightweight_domain_context(self) -> str:
        """Build lightweight domain summary for A-code generation."""
        if not self.domain_info:
            return ""

        domain = self.domain_info.get('domain', {})
        lines = ["\n=== DOMAIN CONTEXT ==="]
        lines.append(f"Domain: {domain.get('name', 'Unknown')}")
        lines.append(f"Content type: {domain.get('content_type', 'Unknown')}")
        lines.append(f"Task complexity: {domain.get('task_complexity', 'Unknown')}")
        return '\n'.join(lines)

    def _get_domain_error_seed_context(self) -> str:
        """Build rich domain error seed context for C-code generation.

        Uses common_error_patterns, domain_terminology.error_associations,
        subdomains, and correctness_criteria from Step 1 as scaffolding
        for reasoning failure categories.
        """
        if not self.domain_info:
            return ""

        lines = ["\n=== DOMAIN ERROR PATTERNS (from domain analysis) ==="]

        # Subdomains — ensure coverage across ALL of them
        subdomains = self.domain_info.get('subdomains', [])
        if subdomains:
            lines.append(f"\nSUBDOMAINS in this domain: {', '.join(subdomains)}")
            lines.append("IMPORTANT: Generate C codes that cover reasoning failures across ALL these")
            lines.append("subdomains, not just the most common ones. Each subdomain may have its own")
            lines.append("characteristic error types. If a subdomain has distinctive reasoning patterns")
            lines.append("(e.g., spatial reasoning, inequality chains, inductive proofs), ensure those")
            lines.append("failure modes are represented.")

        # Common error patterns — the primary seed
        patterns = self.domain_info.get('common_error_patterns', [])
        if patterns:
            lines.append("\nKnown error patterns in this domain:")
            for p in patterns:
                name = p.get('name', '')
                desc = p.get('description', '')
                hints = p.get('detection_hints', [])
                lines.append(f"  - {name}: {desc}")
                if hints:
                    for h in hints[:2]:
                        lines.append(f"      detection hint: {h}")
            lines.append("\nThese known patterns are a STARTING POINT, not a complete list.")
            lines.append("You must also identify error types NOT listed above that are common")
            lines.append("in the subdomains. Consider:")
            lines.append("  - Errors specific to each subdomain's characteristic techniques")
            lines.append("  - Errors in logical structure (proof direction, quantifier scope, etc.)")
            lines.append("  - Errors in algebraic/symbolic manipulation (sign errors, invalid transforms)")
            lines.append("  - Errors in applying standard inequalities or estimates")
            lines.append("  - Errors in geometric or spatial reasoning if applicable")
            lines.append("  - Errors in proof strategy (proving wrong direction, circular reasoning)")

        # Domain terminology with error associations
        terms = self.domain_info.get('domain_terminology', [])
        error_terms = [t for t in terms if t.get('error_associations')]
        if error_terms:
            lines.append("\nDomain concepts with known error-prone usage:")
            for t in error_terms[:10]:
                term = t.get('term', '')
                meaning = t.get('meaning', '')
                assocs = t.get('error_associations', [])
                lines.append(f"  - {term} ({meaning})")
                for a in assocs[:2]:
                    lines.append(f"      common error: {a}")

        # Correctness criteria — what constitutes a valid answer
        criteria = self.domain_info.get('correctness_criteria', [])
        if criteria:
            lines.append("\nCorrectness criteria (violations = potential C codes):")
            for c in criteria:
                name = c.get('criterion', '')
                desc = c.get('description', '')
                lines.append(f"  - {name}: {desc}")

        return '\n'.join(lines)

    def _get_capabilities_context(self) -> str:
        """Build capabilities context for B-code generation."""
        caps = self.structure_info.get('capabilities', {})
        if not caps:
            return ""

        lines = ["\n=== AGENT CAPABILITIES ==="]
        style = caps.get('interaction_style', 'direct_reasoning')
        lines.append(f"Primary interaction style: {style}")

        if style == "tool_calling":
            lines.append("Agents primarily interact with external tools/APIs to accomplish tasks.")
            lines.append("B codes should focus on quality of TOOL USAGE and DECISION-MAKING:")
            lines.append("  - Did the agent select the right tool for the situation?")
            lines.append("  - Did it pass correct arguments?")
            lines.append("  - Did it correctly interpret tool responses?")
            lines.append("  - Did it follow required procedures (e.g., confirmation before action)?")
            lines.append("  - Did it chain tool calls in the right sequence?")
        elif style == "code_execution":
            lines.append("Agents write and execute code to accomplish tasks.")
            lines.append("B codes should focus on quality of CODE and APPROACH:")
            lines.append("  - Is the code correct for the problem?")
            lines.append("  - Does it handle edge cases?")
            lines.append("  - Is the approach appropriate?")
        elif style == "mixed":
            lines.append("Agents use a mix of direct reasoning and tool/API calls.")

        tool_names = caps.get('tool_names_seen', [])
        if tool_names:
            lines.append(f"\nTools/APIs available: {', '.join(tool_names[:15])}")

        return '\n'.join(lines)

    def _get_stage_prompt(self, stage_name: str, base_codes: List[Dict]) -> str:
        """Get stage-specific prompt content."""
        trace_ctx = self._get_trace_format_context()
        agent_ctx = self._get_agent_context()
        domain_ctx = self._get_domain_context()

        base_names = [c.get('name', '') for c in base_codes]

        if self.category == "A":
            arch_ctx = self._get_architecture_context()
            domain_lite = self._get_lightweight_domain_context()
            caps_ctx = self._get_capabilities_context()

            if stage_name == "Architectural":
                signal_ctx = self._get_signal_context()
                return f"""CATEGORY A - System Failures (Agent-Independent)

These are failures that can happen to ANY agent regardless of role.
NOT about correctness — about system-level issues that prevent agents from
functioning properly or producing usable output.

NAMING RULE: A-codes must NEVER contain agent role names ({', '.join(r.capitalize() for r in self._get_active_roles())}).
ROLE-NEUTRALITY RULE: A-codes must describe GENERIC system failures, not failures specific to
one agent's purpose. Apply the "swap test": if replacing the agent with a different-role agent
would make the code inapplicable, it belongs in B, not A. For example:
  - GOOD A code: "Output truncation" — any agent can produce truncated output
  - GOOD A code: "Inter-agent information loss" — any handoff can lose information
  - BAD A code: "Verdict misreporting" — only a checker produces verdicts → this is B
  - BAD A code: "Refinement inconsistency" — only a refiner refines → this is B
  - BAD A code: "Coordination decision error" — only a coordinator coordinates → this is B

YOUR TASK (Architectural Risk Analysis):
Given the system architecture below, identify ALL plausible system-level failure
modes that could occur in this pipeline. Think about:
- What happens at each handoff point? What can go wrong?
- What happens if an agent runs too long, or its context fills up?
- What happens if an agent produces no output, or garbled output?
- What if an agent contradicts itself or loops?
- What if an agent refuses to engage or abandons the task?
- What if the pipeline terminates prematurely?

You do NOT need to see traces for this — reason purely from the architecture.
Generate codes for failures that are PLAUSIBLE based on how this system is designed.

For each code, set "evidence": "theoretical" since these come from architectural
reasoning rather than observed trace data.

{A_FAILURE_CATEGORIES}

{arch_ctx}
{caps_ctx}
{domain_lite}

{signal_ctx if signal_ctx else ""}
"""
            else:  # Empirical
                signal_ctx = self._get_signal_context()
                return f"""CATEGORY A - System Failures (Agent-Independent)

These are failures that can happen to ANY agent regardless of role.
NOT about correctness — about system-level issues that prevent agents from
functioning properly or producing usable output.

NAMING RULE: A-codes must NEVER contain agent role names ({', '.join(r.capitalize() for r in self._get_active_roles())}).
ROLE-NEUTRALITY RULE: A-codes must describe GENERIC system failures, not failures specific to
one agent's purpose. Apply the "swap test": if replacing the agent with a different-role agent
would make the code inapplicable, it belongs in B, not A.

YOUR TASK (Empirical Behavioral Analysis):
Analyze the BEHAVIORAL SIGNALS extracted from all traces (below) and the
SAMPLE TRACES to identify system failures that ACTUALLY OCCURRED.

Focus on:
- Behavioral anomalies: looping, repetition, refusal, degrading quality
- Output issues: truncation, empty output, malformed responses
- Communication issues: information lost between agents, handoff failures
- Any system-level problems visible in the trace content

Do NOT generate codes for trace FORMAT validation rules (e.g., "missing tag X"
or "wrong delimiter Y"). Focus on the underlying system failures, not their
surface-level formatting symptoms.

For each code, set "evidence": "observed" since these come from actual trace data.

{A_FAILURE_CATEGORIES}

{signal_ctx}

{arch_ctx}
{caps_ctx}
{domain_lite}
"""

        elif self.category == "B":
            active_roles = self._get_active_roles()
            arch_ctx = self._get_architecture_context()
            caps_ctx = self._get_capabilities_context()

            # Build agent list per role for agent_heuristics generation
            agents_info = self.structure_info.get('discovered_agents', {})
            role_details = agents_info.get('role_details', {})
            agents_per_role = {}
            for role in active_roles:
                details = role_details.get(role, {})
                agent_list = details.get('agents', [])[:5]
                if agent_list:
                    agents_per_role[role] = agent_list

            # Build dynamic role definitions and naming rule
            role_defs_text = "\n".join(
                f"- {role}: {role_details.get(role, {}).get('definition', 'N/A')}"
                for role in active_roles
            )
            role_name_prefixes = ", ".join(f"{r.capitalize()}_" for r in active_roles)
            b_role_guidance = build_b_role_guidance(role_details)

            return f"""CATEGORY B - Role-Specific Quality Failures

NAMING RULE: B-codes MUST contain role name prefix ({role_name_prefixes})

ROLE DEFINITIONS:
{role_defs_text}

ACTIVE ROLES (only generate for these): {active_roles}

DISCOVERED AGENTS PER ROLE:
{json.dumps(agents_per_role, indent=2)}

{b_role_guidance}

YOUR TASK ({stage_name} Stage):
{"Analyze the system ARCHITECTURE and identify role-specific quality failures based on how agents interact and what decisions they make." if stage_name == "Theoretical" else "Analyze ACTUAL TRACE CONTENT and find role-specific quality failures that occurred."}

Generate codes for complete coverage of all distinct quality failure modes per active role.
Each code must represent a genuinely distinct failure — do NOT create multiple codes
for variants of the same quality problem.

{caps_ctx}
{arch_ctx}
{trace_ctx}
"""

        else:  # C
            domain_error_ctx = self._get_domain_error_seed_context()

            if stage_name == "Domain-Seeded":
                return f"""CATEGORY C - Domain Reasoning Failures

These are failures in the REASONING PROCESS itself, specific to the problem domain.
C codes describe WHAT went wrong in the reasoning — not WHO made the error or WHETHER
the system broke. A judge should be able to identify these by reading the reasoning
in a trace, WITHOUT needing to independently solve the problem or know the correct answer.

NAMING RULE: C-codes must NEVER contain agent role names ({', '.join(r.capitalize() for r in self._get_active_roles())}).
A valid C code applies equally whether the flawed reasoning appeared in any agent's output.

YOUR TASK (Domain-Seeded Stage):
Using the domain error patterns, subdomains, and terminology pitfalls below as scaffolding,
generate categories of reasoning failure that:
1. Are DETECTABLE from the trace alone — a judge reading the reasoning can spot the flaw
   without solving the problem independently
2. Describe ERROR TYPES, not error instances — each code should apply across many problems,
   not just one specific scenario
3. Are at the right GRANULARITY — not too broad ("mathematical error") and not too narrow
   ("forgot to check n=0 in induction")
4. COVER ALL SUBDOMAINS — ensure each subdomain's characteristic reasoning failures are
   represented. Don't cluster all codes around one subdomain while ignoring others.

COVERAGE CHECK: After generating codes, verify you have at least one code relevant to
each subdomain listed below. If a subdomain has no coverage, add a code for its most
common reasoning failure type.

For each code, provide a concrete example of when it applies vs when it does NOT,
to ensure the code is operationally distinguishable from other codes.

DISTINGUISHABILITY RULE: If two codes cannot be told apart by a judge reading a trace,
they must be merged into one code.

{domain_error_ctx}
{domain_ctx}
"""
            else:  # Empirical
                return f"""CATEGORY C - Domain Reasoning Failures

These are failures in the REASONING PROCESS itself, specific to the problem domain.
C codes describe WHAT went wrong in the reasoning — not WHO made the error or WHETHER
the system broke. A judge should be able to identify these by reading the reasoning
in a trace, WITHOUT needing to independently solve the problem or know the correct answer.

NAMING RULE: C-codes must NEVER contain agent role names ({', '.join(r.capitalize() for r in self._get_active_roles())}).

YOUR TASK (Trace-Grounded Stage):
Analyze the SAMPLE TRACES below. For each trace where reasoning appears flawed,
identify WHAT TYPE of reasoning error is present. Then cluster these into distinct
categories of reasoning failure.

Focus on patterns of flawed reasoning that are DETECTABLE from the trace content:
- Internal contradictions within the reasoning
- Unjustified logical leaps or unsupported claims
- Misapplication of domain concepts or techniques
- Gaps in case analysis or missing considerations
- Incorrect manipulation of domain-specific objects (formulas, data structures, etc.)
- Errors in algebraic or symbolic transformations (sign errors, invalid cancellations)
- Wrong direction of inequalities or estimates
- Geometric/spatial reasoning errors (wrong angle relations, invalid similarity claims)
- Proof structure errors (proving only one direction, assuming the conclusion)
- Logical errors (quantifier confusion, affirming the consequent)

Do NOT generate codes for:
- System-level failures (timeouts, crashes, truncation) — those are A codes
- Agent role failures (weak validation, wrong routing) — those are B codes
- Outcome-level judgments ("answer is wrong") — C codes describe the PROCESS flaw

{domain_error_ctx}
{domain_ctx}
"""

    def _run_stage(self, traces: List[Dict], stage_name: str,
                   base_codes: List[Dict], existing_codes: Dict) -> List[Dict]:
        """Run one stage of code generation."""
        progress(f"  Running {self.category}-{stage_name}...")

        # For A-Architectural stage: no traces needed (pure architectural reasoning)
        # For A-Empirical stage: provide more traces with better slicing
        if self.category == "A" and stage_name == "Architectural":
            traces_text = ""  # No traces for architectural reasoning
        else:
            traces_text = "\n\n".join([format_trace(t, max_length=3000) for t in traces[:15]])

        existing_str = ""
        if existing_codes:
            existing_str = "\nEXISTING CODES FROM OTHER CATEGORIES (don't duplicate concepts):\n"
            for cat_key, codes in existing_codes.items():
                if codes:
                    names = [c.get('name', '')[:40] for c in (codes if isinstance(codes, list) else codes.values())][:8]
                    existing_str += f"  {cat_key}: {', '.join(names)}\n"

        # Build evidence field instruction for A codes
        evidence_field = ""
        if self.category == "A":
            evidence_field = ', "evidence": "theoretical|observed"'

        # Build requirements based on whether we have base codes
        if base_codes:
            requirements = f"""REQUIREMENTS:
1. Generate NEW codes as needed for complete coverage (not duplicating base codes)
2. Only add codes if they represent distinct failure modes not already covered
3. Each code needs detection_heuristics grounded in observable signals
4. Definitions should be concise and clear
5. Follow naming rules strictly
6. Prioritize clarity over quantity — fewer clear codes is better than many overlapping ones"""
        elif self.category == "C":
            requirements = f"""REQUIREMENTS:
1. Each code describes an ERROR TYPE (not an error instance) — it should apply across
   many problems, not just one specific scenario
2. Each code must be DETECTABLE by a judge reading the trace — the judge should NOT need
   to solve the problem independently or know the correct answer
3. Each code must be DISTINGUISHABLE from every other code — if two codes cannot be told
   apart by a judge, merge them
4. Do NOT include system failures (A codes) or role-specific failures (B codes)
5. C codes must NEVER reference agent roles ({', '.join(self._get_active_roles())})
6. detection_heuristics must describe what a judge would look for in the trace text
7. Definitions should be concise and clear
8. Prioritize clarity over quantity — fewer clear codes is better than many overlapping ones"""
        else:
            requirements = f"""REQUIREMENTS:
1. Generate codes for complete coverage of all distinct system failure modes
2. Each code must represent a genuinely distinct failure — do NOT create multiple
   codes for variants of the same problem (e.g., do not have separate codes for
   "output missing" and "output empty" — those are the same failure)
3. Prefer CAUSAL codes over SYMPTOM codes. "Token limit caused truncation" is one
   code, not two separate codes for "token limit hit" and "output truncated"
4. Each code needs detection_heuristics grounded in observable signals
5. Definitions should be concise and clear
6. Follow naming rules strictly"""

        # Build traces section
        traces_section = ""
        if traces_text:
            traces_section = f"\nSAMPLE TRACES:\n{traces_text}"

        prompt = f"""You are the {stage_name} Agent generating Category {self.category} codes.

{self._get_stage_prompt(stage_name, base_codes)}
{existing_str}
{traces_section}

{requirements}

OUTPUT JSON:
{{
  "codes": [
    {{
      "code": "{self.category}.X",
      "name": "Descriptive_Name",
      "definition": "Concise definition.",
      "when_to_use": "When to apply",
      "when_not_to_use": "When NOT to apply",
      "detection_heuristics": ["observable signal from trace content", "..."],
      "severity": "critical|major|minor"{evidence_field}
      {f', "applies_to_role": "{"|".join(self._get_active_roles())}", "agent_heuristics": {{"AgentName": ["agent-specific signal"]}}' if self.category == 'B' else ''}
    }}
  ]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)

            if isinstance(result, list):
                codes = result
            elif isinstance(result, dict):
                codes = result.get('codes', [])
            else:
                codes = []

            valid_codes = [c for c in codes if isinstance(c, dict)]
            return valid_codes

        except Exception as e:
            progress(f"  [!] {stage_name} error: {e}")
            return []

    def _merge_codes(self, base_codes: List[Dict],
                     stage1_codes: List[Dict], stage2_codes: List[Dict]) -> List[Dict]:
        """Merge codes from both stages, deduplicating aggressively."""
        # Filter valid dicts
        base_codes = [c for c in base_codes if isinstance(c, dict)]
        stage1_codes = [c for c in stage1_codes if isinstance(c, dict)]
        stage2_codes = [c for c in stage2_codes if isinstance(c, dict)]

        all_codes = base_codes + stage1_codes + stage2_codes

        if not all_codes:
            return []

        # If only base codes exist (no new codes generated), just normalize
        if not stage1_codes and not stage2_codes:
            return normalize_code_ids(base_codes, self.category)

        # Use LLM to deduplicate all codes against each other
        def summarize(codes: List[Dict]) -> List[Dict]:
            return [{
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
                "evidence": c.get("evidence", ""),
            } for c in codes if isinstance(c, dict)]

        # Different prompts based on whether we have base codes
        if base_codes:
            base_section = f"""BASE CODES (keep all, these are universal):
{json.dumps(summarize(base_codes), indent=2)}

GENERATED CODES TO FILTER (remove duplicates of base or each other):
{json.dumps(summarize(stage1_codes + stage2_codes), indent=2)}

Keep all base codes. Return only generated codes that are NOT duplicates of base codes.
Also merge any generated codes that duplicate each other."""
        else:
            base_section = f"""ALL GENERATED CODES (from two analysis stages):
{json.dumps(summarize(all_codes), indent=2)}"""

        prompt = f"""Deduplicate these Category {self.category} codes.

{base_section}

DEDUPLICATION RULES:
1. Two codes are duplicates if they describe the SAME underlying failure, even if
   worded differently. E.g., "output missing" and "no output produced" are duplicates.
2. If one code describes a CAUSE and another describes its SYMPTOM, keep the CAUSAL
   code and remove the symptom code. E.g., keep "token limit exhaustion" over
   "output truncated mid-sentence" — the truncation is a symptom of the token limit.
3. When merging duplicates, prefer the code with more specific detection_heuristics
   or the one with "evidence": "observed" over "evidence": "theoretical".
4. Be aggressive about merging — it is better to have fewer distinct codes than
   many overlapping ones.

OUTPUT JSON:
{{
  "kept_codes": [
    {{"name": "...", "definition": "..."}}
  ],
  "removed": [{{"name": "...", "reason": "Duplicate of / symptom of ..."}}]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)

            kept_names = {c.get('name', '').lower() for c in result.get('kept_codes', [])}

            # Keep codes that passed filter
            kept = [c for c in all_codes if c.get('name', '').lower() in kept_names]

            # If filter removed everything, fall back to all codes
            if not kept:
                progress(f"  [!] Dedup removed all codes, falling back")
                kept = all_codes

            return normalize_code_ids(kept, self.category)

        except Exception as e:
            progress(f"  [!] Merge error: {e}")
            return normalize_code_ids(all_codes, self.category)


# =============================================================================
# STEP 6: CROSS-CATEGORY DEDUPLICATOR
# =============================================================================

class CrossCategoryDeduplicator:
    """Ensure no concept overlap between A, B, and C categories."""

    def __init__(self, client: TextProvider):
        self.client = client

    def deduplicate(self, a_codes: List[Dict], b_codes: List[Dict], c_codes: List[Dict]) -> Dict:
        progress("\nStep 6: Cross-Category Deduplicator")

        a_codes = [c for c in a_codes if isinstance(c, dict)]
        b_codes = [c for c in b_codes if isinstance(c, dict)]
        c_codes = [c for c in c_codes if isinstance(c, dict)]

        def summarize(codes: List[Dict]) -> List[Dict]:
            return [{
                "code": c.get("code", ""),
                "name": str(c.get("name", ""))[:60],
                "definition": truncate_text(str(c.get("definition", "")), 150)
            } for c in codes if isinstance(c, dict)]

        prompt = f"""Review codes across all three categories for semantic duplicates.

CATEGORY RULES:
A: System failures - agent-independent (can happen to ANY agent)
B: Role-specific QUALITY failures - WHO did their job wrong
C: Domain reasoning failures - WHY the reasoning is wrong

CRITICAL BOUNDARY RULES — read carefully before marking duplicates:
1. An A code and a B code that describe the SAME EVENT from different levels of analysis
   are NOT duplicates. Example: "Inter-agent information loss" (A) and "Checker performs
   weak validation" (B) may co-occur but describe different things — the system-level
   symptom vs the role-specific cause.
2. A B code is a duplicate of another B code ONLY if they describe the same quality failure
   for the same role. B codes for DIFFERENT roles are never duplicates of each other.
3. A C code is a duplicate of another code ONLY if it describes the exact same reasoning
   error type. A C code describing a reasoning flaw is NOT a duplicate of a B code
   describing a role doing its job poorly, even if the reasoning flaw is what caused
   the role failure.
4. Cross-category duplicates (A↔B, A↔C, B↔C) should be RARE. Only mark as duplicate
   when codes are truly synonymous — same concept, same granularity, same perspective.

CATEGORY A:
{json.dumps(summarize(a_codes), indent=2)}

CATEGORY B:
{json.dumps(summarize(b_codes), indent=2)}

CATEGORY C:
{json.dumps(summarize(c_codes), indent=2)}

Find semantic duplicates. Each concept in exactly ONE category.
Remember: cross-category duplicates should be rare. Most duplicates will be WITHIN a category.

OUTPUT JSON:
{{
  "duplicates_found": [{{"concept": "...", "found_in": ["A.1", "B.3"], "keep_in": "A.1", "remove": "B.3"}}]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)

            duplicates = result.get('duplicates_found', [])
            if duplicates:
                progress(f"  Duplicates found: {len(duplicates)}")

            # Remove duplicates from the ORIGINAL full codes (preserving all fields)
            codes_to_remove = set()
            for dup in duplicates:
                remove_code = dup.get("remove", "")
                if remove_code:
                    codes_to_remove.add(remove_code)

            filtered_a = [c for c in a_codes if c.get("code", "") not in codes_to_remove]
            filtered_b = [c for c in b_codes if c.get("code", "") not in codes_to_remove]
            filtered_c = [c for c in c_codes if c.get("code", "") not in codes_to_remove]

            return {
                "category_a": filtered_a,
                "category_b": filtered_b,
                "category_c": filtered_c,
                "duplicates_found": duplicates
            }
        except Exception as e:
            progress(f"  [!] Deduplication error: {e}")
            return {
                "category_a": a_codes,
                "category_b": b_codes,
                "category_c": c_codes,
                "duplicates_found": []
            }


# =============================================================================
# STEP 7: CROSS-CATEGORY VALIDATOR
# =============================================================================

class CrossCategoryValidator:
    """Validate codes are in the correct category and fix misplacements."""

    def __init__(self, client: TextProvider, structure_info: Dict):
        self.client = client
        self.structure_info = structure_info

    def validate(self, a_codes: List[Dict], b_codes: List[Dict], c_codes: List[Dict]) -> Dict:
        progress("\nStep 7: Cross-Category Validator")

        a_codes = [c for c in a_codes if isinstance(c, dict)]
        b_codes = [c for c in b_codes if isinstance(c, dict)]
        c_codes = [c for c in c_codes if isinstance(c, dict)]

        agents = self.structure_info.get('discovered_agents', {})
        agent_names = agents.get('agents', [])[:10]
        role_details = agents.get('role_details', {})
        role_names = list(role_details.keys())

        def summarize(codes: List[Dict]) -> List[Dict]:
            return [{
                "code": c.get("code", ""),
                "name": str(c.get("name", ""))[:60],
                "definition": truncate_text(str(c.get("definition", "")), 150),
                "applies_to_role": c.get("applies_to_role", "")
            } for c in codes if isinstance(c, dict)]

        prompt = f"""Validate codes against strict category rules.

DISCOVERED AGENTS: {agent_names}
ROLE TYPES: {role_names}

VALIDATION RULES:
A: System failures - NO role names in code name, about mechanical failures
B: Role quality failures - MUST have role name in code name, about incorrect work
C: Reasoning failures - NO role names, about domain-specific logic errors

CATEGORY A:
{json.dumps(summarize(a_codes), indent=2)}

CATEGORY B:
{json.dumps(summarize(b_codes), indent=2)}

CATEGORY C:
{json.dumps(summarize(c_codes), indent=2)}

Fix any violations. Move misplaced codes to correct category.

OUTPUT JSON:
{{
  "violations_fixed": [
    {{
      "code": "X.Y",
      "issue": "description of the problem",
      "action": "what was done",
      "move_to": "a|b|c or null if no move needed",
      "new_name": "updated name or null if unchanged",
      "applies_to_role": "role name or null if unchanged"
    }}
  ]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)

            violations = result.get('violations_fixed', [])
            progress(f"  Violations fixed: {len(violations)}")

            # Build lookup of original codes by code ID
            all_codes = {}
            for c in a_codes:
                all_codes[c.get("code", "")] = c
            for c in b_codes:
                all_codes[c.get("code", "")] = c
            for c in c_codes:
                all_codes[c.get("code", "")] = c

            # Apply violations to original codes
            codes_to_move = {}  # code_id -> target category
            for v in violations:
                code_id = v.get("code", "")
                if code_id not in all_codes:
                    continue
                if v.get("new_name"):
                    all_codes[code_id]["name"] = v["new_name"]
                if v.get("applies_to_role"):
                    all_codes[code_id]["applies_to_role"] = v["applies_to_role"]
                if v.get("move_to"):
                    codes_to_move[code_id] = v["move_to"]

            # Rebuild categories, moving codes as needed
            final_a = [c for c in a_codes if c.get("code", "") not in codes_to_move]
            final_b = [c for c in b_codes if c.get("code", "") not in codes_to_move]
            final_c = [c for c in c_codes if c.get("code", "") not in codes_to_move]

            for code_id, target in codes_to_move.items():
                code_obj = all_codes[code_id]
                if target == "a":
                    final_a.append(code_obj)
                elif target == "b":
                    final_b.append(code_obj)
                elif target == "c":
                    final_c.append(code_obj)

            return {
                "category_a": final_a,
                "category_b": final_b,
                "category_c": final_c,
                "violations_fixed": violations
            }
        except Exception as e:
            progress(f"  [!] Validation error: {e}")
            return {
                "category_a": a_codes,
                "category_b": b_codes,
                "category_c": c_codes,
                "violations_fixed": []
            }


# =============================================================================
# STEP 8: TAXONOMY CHECKER
# =============================================================================

class TaxonomyChecker:
    """Final validation and fixing step."""

    # Placeholder heuristic patterns that indicate low-quality / templated output
    PLACEHOLDER_PATTERNS = [
        r"trace_field_\d",
        r"trace_field\d",
        r"field_\d+",
        r"trace\.field",
        r"\bTBD\b",
        r"\bTODO\b",
        r"\bplaceholder\b",
        r"\.{3,}",   # "..." as filler
    ]

    # Keywords associated with each failure category for coverage checking
    FAILURE_CATEGORY_KEYWORDS = {
        "output_issues": [
            "output", "empty", "truncat", "malform", "no response", "garble",
            "missing output", "partial output", "incomplete output",
        ],
        "context_memory": [
            "context", "memory", "overflow", "forgot", "contradict",
            "lost track", "window", "capacity", "re-deriv",
        ],
        "communication": [
            "handoff", "communication", "passed", "routing", "downstream",
            "upstream", "inter-agent", "misroute", "relay",
        ],
        "behavioral": [
            "loop", "repetit", "refusal", "abandon", "circular",
            "degrad", "stuck", "regress", "pathological",
        ],
        "execution": [
            "timeout", "crash", "error", "exception", "rate limit",
            "resource", "runtime", "api error", "fail",
        ],
        "instruction": [
            "instruction", "compliance", "system prompt", "ignored constraint",
            "wrong problem", "format requirement", "disobey", "non-compliance",
        ],
        "tool_api": [
            "tool", "api", "function call", "tool call", "wrong tool",
            "wrong argument", "tool error", "tool response", "tool fail",
            "malformed argument", "invoke", "tool misuse",
        ],
    }

    def __init__(self, client: TextProvider, structure_info: Dict, domain_info: Dict):
        self.client = client
        self.structure_info = structure_info
        self.domain_info = domain_info
        self._placeholder_re = [re.compile(p, re.IGNORECASE) for p in self.PLACEHOLDER_PATTERNS]
        # Get role types from discovered agents
        agents_info = structure_info.get('discovered_agents', {})
        self.role_types = list(agents_info.get('role_details', {}).keys())

    def check_and_fix(self, a_codes: List[Dict], b_codes: List[Dict], c_codes: List[Dict]) -> Dict:
        progress("\nStep 8: Taxonomy Checker")

        issues = {"a": [], "b": [], "c": []}

        progress("  Checking A codes...")
        for code in a_codes:
            code_issues = self._check_a_code(code)
            if code_issues:
                issues["a"].append({"code": code.get("code"), "issues": code_issues})

        # A-code coverage check: verify failure categories are represented
        coverage_gaps = self._check_a_coverage(a_codes)
        if coverage_gaps:
            progress(f"  A-code coverage gaps: {coverage_gaps}")

        # A-code overlap check: detect semantically overlapping codes
        overlaps = self._check_a_overlaps(a_codes)
        if overlaps:
            progress(f"  A-code overlaps detected: {len(overlaps)}")

        progress("  Checking B codes...")
        for code in b_codes:
            code_issues = self._check_b_code(code)
            if code_issues:
                issues["b"].append({"code": code.get("code"), "issues": code_issues})

        # B-code coverage check: verify each active role has B codes
        b_coverage_gaps = self._check_b_coverage(b_codes)
        if b_coverage_gaps:
            progress(f"  B-code coverage gaps (missing roles): {b_coverage_gaps}")

        # B-code overlap check: detect semantically overlapping codes
        b_overlaps = self._check_b_overlaps(b_codes)
        if b_overlaps:
            progress(f"  B-code overlaps detected: {len(b_overlaps)}")

        progress("  Checking C codes...")
        for code in c_codes:
            code_issues = self._check_c_code(code)
            if code_issues:
                issues["c"].append({"code": code.get("code"), "issues": code_issues})

        # C-code coverage check: verify subdomains are represented
        c_coverage_gaps = self._check_c_coverage(c_codes)
        if c_coverage_gaps:
            progress(f"  C-code coverage gaps (missing subdomains): {c_coverage_gaps}")

        # C-code overlap check
        c_overlaps = self._check_c_overlaps(c_codes) if len(c_codes) >= 2 else []
        if c_overlaps:
            progress(f"  C-code overlaps detected: {len(c_overlaps)}")

        total_issues = len(issues["a"]) + len(issues["b"]) + len(issues["c"])
        progress(f"  Found {total_issues} codes with issues")

        if total_issues > 0:
            progress("  Fixing issues...")
            a_codes = self._fix_codes(a_codes, issues["a"], "A")
            b_codes = self._fix_codes(b_codes, issues["b"], "B")
            c_codes = self._fix_codes(c_codes, issues["c"], "C")

        # Fix coverage gaps and overlaps for A codes (after individual fixes)
        if coverage_gaps or overlaps:
            progress("  Fixing A-code coverage gaps and overlaps...")
            a_codes = self._fix_a_coverage_and_overlaps(a_codes, coverage_gaps, overlaps)

        # Fix coverage gaps and overlaps for B codes (after individual fixes)
        if b_coverage_gaps or b_overlaps:
            progress("  Fixing B-code coverage gaps and overlaps...")
            b_codes = self._fix_b_coverage_and_overlaps(b_codes, b_coverage_gaps, b_overlaps)

        # Fix coverage gaps and overlaps for C codes (after individual fixes)
        if c_coverage_gaps or c_overlaps:
            progress("  Fixing C-code coverage gaps and overlaps...")
            c_codes = self._fix_c_coverage_and_overlaps(c_codes, c_coverage_gaps, c_overlaps)

        return {
            "category_a": a_codes,
            "category_b": b_codes,
            "category_c": c_codes,
            "issues_found": issues,
            "a_coverage_gaps": coverage_gaps,
            "a_overlaps_detected": overlaps,
            "b_coverage_gaps": b_coverage_gaps,
            "b_overlaps_detected": b_overlaps,
            "c_coverage_gaps": c_coverage_gaps,
            "c_overlaps_detected": c_overlaps,
            "total_issues": total_issues
        }

    def _check_a_code(self, code: Dict) -> List[str]:
        """Check A code against rules — structural, quality, and content checks."""
        issues = []
        name = code.get("name", "").lower()

        # Rule: name must not contain role types
        for role_type in self.role_types:
            if role_type in name:
                issues.append(f"Name contains role type '{role_type}' (not allowed for A codes)")
                break

        # Rule: definition must exist and be substantive
        definition = code.get("definition", "")
        if not definition or len(definition) < 10:
            issues.append("Missing or too short definition")

        # Rule: detection_heuristics must exist
        heuristics = code.get("detection_heuristics", [])
        if not heuristics:
            issues.append("Missing detection heuristics")
        else:
            # Quality: check for placeholder/templated heuristics
            placeholder_count = 0
            for h in heuristics:
                if isinstance(h, str):
                    for pattern in self._placeholder_re:
                        if pattern.search(h):
                            placeholder_count += 1
                            break
            if placeholder_count > 0:
                issues.append(f"Has {placeholder_count} placeholder/templated heuristic(s) — "
                              f"heuristics must be grounded in observable signals")

            # Quality: heuristics should not all be very short/vague
            short_count = sum(1 for h in heuristics if isinstance(h, str) and len(h) < 15)
            if short_count == len(heuristics) and len(heuristics) > 0:
                issues.append("All heuristics are very short — need more specific detection signals")

        # Rule: when_to_use / when_not_to_use required
        if not code.get("when_to_use"):
            issues.append("Missing when_to_use")
        if not code.get("when_not_to_use"):
            issues.append("Missing when_not_to_use")

        # Rule: evidence field should be present for A codes
        evidence = code.get("evidence", "")
        if evidence and evidence not in ("theoretical", "observed"):
            issues.append(f"Invalid evidence value '{evidence}' — must be 'theoretical' or 'observed'")

        return issues

    def _check_a_coverage(self, a_codes: List[Dict]) -> List[str]:
        """Check that A codes cover all expected failure categories.

        Skips categories that are irrelevant based on detected capabilities:
        - tool_api: skipped when no tool calling detected
        - communication: skipped for single-agent systems (no handoffs)
        """
        # Build a combined text of all A code names + definitions
        all_text = ""
        for code in a_codes:
            all_text += " " + code.get("name", "").lower()
            all_text += " " + code.get("definition", "").lower()
            all_text += " " + code.get("when_to_use", "").lower()

        # Determine which categories to skip based on capabilities
        caps = self.structure_info.get("capabilities", {})
        interaction_style = caps.get("interaction_style", "direct_reasoning")
        agents = self.structure_info.get("agents", [])
        skip_categories = set()

        if interaction_style == "direct_reasoning" and not caps.get("has_tool_calling", False):
            skip_categories.add("tool_api")

        if len(agents) <= 1:
            skip_categories.add("communication")

        gaps = []
        for category, keywords in self.FAILURE_CATEGORY_KEYWORDS.items():
            if category in skip_categories:
                continue
            found = any(kw in all_text for kw in keywords)
            if not found:
                gaps.append(category)

        return gaps

    def _check_a_overlaps(self, a_codes: List[Dict]) -> List[Dict]:
        """Detect potentially overlapping A codes using LLM analysis."""
        if len(a_codes) < 2:
            return []

        def summarize(codes: List[Dict]) -> List[Dict]:
            return [{
                "code": c.get("code", ""),
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
            } for c in codes if isinstance(c, dict)]

        prompt = f"""Analyze these Category A codes for semantic overlaps.

Two codes OVERLAP if they describe the same underlying failure mode, even if
worded differently. For example, "Output_Missing" and "No_Response_Produced"
are overlaps. Also flag cases where one code is a SYMPTOM of another.

A CODES:
{json.dumps(summarize(a_codes), indent=2)}

For each overlap pair, explain why they overlap and which should be kept.
If no overlaps exist, return an empty list.

OUTPUT JSON:
{{
  "overlaps": [
    {{
      "code1": "A.X",
      "code2": "A.Y",
      "reason": "Both describe the same failure: ...",
      "recommendation": "Keep A.X, merge A.Y into it"
    }}
  ]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)
            return result.get("overlaps", [])
        except Exception as e:
            progress(f"  [!] Overlap check error: {e}")
            return []

    def _fix_a_coverage_and_overlaps(self, a_codes: List[Dict],
                                      coverage_gaps: List[str],
                                      overlaps: List[Dict]) -> List[Dict]:
        """Fix coverage gaps by adding missing codes and resolve overlaps."""
        arch = self.structure_info.get('architecture', {})
        agents = self.structure_info.get('discovered_agents', {})

        # Build architecture summary for context
        arch_summary = f"Topology: {arch.get('topology', 'Unknown')}"
        handoffs = arch.get('critical_handoffs', [])
        if handoffs:
            arch_summary += "\nHandoffs: " + ", ".join(
                f"{h.get('from_agent','?')}->{h.get('to_agent','?')}" for h in handoffs[:5]
            )

        role_details = agents.get('role_details', {})
        roles_summary = ", ".join(
            f"{r}: {len(d.get('agents', []))} agents"
            for r, d in role_details.items() if d.get('agents')
        )

        # Format current codes
        def summarize(codes: List[Dict]) -> List[Dict]:
            return [{
                "code": c.get("code", ""),
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
            } for c in codes if isinstance(c, dict)]

        # Build the fix prompt
        gap_section = ""
        if coverage_gaps:
            category_descriptions = {
                "output_issues": "Output issues (empty, truncated, malformed, unusable output)",
                "context_memory": "Context/memory issues (overflow, context loss, forgetting prior info)",
                "communication": "Inter-agent communication (handoff failures, information lost between agents)",
                "behavioral": "Behavioral anomalies (looping, repetition, refusal, degradation)",
                "execution": "Execution errors (timeouts, crashes, API errors, resource exhaustion)",
                "instruction": "Instruction compliance (ignoring system prompt, wrong problem, format violations)",
                "tool_api": "Tool/API interaction (wrong tool called, wrong arguments, tool errors, misinterpreted responses)",
            }
            gap_descriptions = [category_descriptions.get(g, g) for g in coverage_gaps]
            gap_section = f"""
COVERAGE GAPS — the following failure categories have NO codes:
{chr(10).join(f'  - {d}' for d in gap_descriptions)}

Generate NEW codes to fill these gaps. Each gap needs at least one code."""

        overlap_section = ""
        if overlaps:
            overlap_section = f"""
OVERLAPPING CODES — merge these:
{json.dumps(overlaps, indent=2)}

For each overlap, merge the two codes into one stronger code. Keep the better
name and combine the detection heuristics."""

        prompt = f"""Fix Category A codes: fill coverage gaps and resolve overlaps.

CURRENT A CODES:
{json.dumps(summarize(a_codes), indent=2)}

ARCHITECTURE: {arch_summary}
ROLES: {roles_summary}

{gap_section}
{overlap_section}

NAMING RULE: A-codes must NEVER contain agent role names ({', '.join(r.capitalize() for r in role_details.keys())})

Return the COMPLETE updated list of A codes (existing + new, with overlaps merged).

OUTPUT JSON:
{{
  "codes": [
    {{
      "code": "A.X",
      "name": "Descriptive_Name",
      "definition": "Concise definition.",
      "when_to_use": "When to apply",
      "when_not_to_use": "When NOT to apply",
      "detection_heuristics": ["observable signal", "..."],
      "severity": "critical|major|minor",
      "evidence": "theoretical|observed"
    }}
  ],
  "changes_made": ["Merged A.2 and A.5 into ...", "Added code for ..."]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)

            new_codes = result.get("codes", [])
            changes = result.get("changes_made", [])

            if new_codes and isinstance(new_codes, list):
                valid = [c for c in new_codes if isinstance(c, dict)]
                if valid:
                    progress(f"    Changes: {changes[:5]}")
                    return normalize_code_ids(valid, "A")

            # Fallback: return original codes if fix failed
            return a_codes

        except Exception as e:
            progress(f"  [!] Coverage/overlap fix error: {e}")
            return a_codes

    def _check_b_code(self, code: Dict) -> List[str]:
        """Check B code against rules — structural, quality, and A/B boundary."""
        issues = []
        name = code.get("name", "").lower()
        definition = code.get("definition", "").lower()
        when_to_use = code.get("when_to_use", "").lower()

        # Rule: name must contain role type
        has_role_type = any(role in name for role in self.role_types)
        if not has_role_type:
            role_names_str = ", ".join(r.capitalize() for r in self.role_types)
            issues.append(f"Name must contain role type ({role_names_str})")

        # Rule: applies_to_role required
        if not code.get("applies_to_role"):
            issues.append("Missing applies_to_role field")

        # Rule: definition must exist and be substantive
        if not definition or len(definition) < 10:
            issues.append("Missing or too short definition")

        # Rule: detection_heuristics required
        heuristics = code.get("detection_heuristics", [])
        if not heuristics:
            issues.append("Missing detection heuristics")
        else:
            # Quality: check for placeholder/templated heuristics
            for h in heuristics:
                if isinstance(h, str):
                    for pattern in self._placeholder_re:
                        if pattern.search(h):
                            issues.append("Has placeholder/templated heuristic(s)")
                            break
                    else:
                        continue
                    break

        # Rule: when_to_use / when_not_to_use required
        if not code.get("when_to_use"):
            issues.append("Missing when_to_use")
        if not code.get("when_not_to_use"):
            issues.append("Missing when_not_to_use")

        # CRITICAL: A/B boundary check — B code must not describe system/output failures
        combined_text = f"{name} {definition} {when_to_use}"
        for keyword in B_CODE_A_TYPE_KEYWORDS:
            if keyword in combined_text:
                issues.append(f"B code appears to describe a system/output failure "
                              f"('{keyword}') — this belongs in Category A, not B. "
                              f"B codes are only about quality of work done, not about "
                              f"whether output was produced.")
                break

        return issues

    def _check_b_coverage(self, b_codes: List[Dict]) -> List[str]:
        """Check that each active role has at least one B code."""
        agents = self.structure_info.get('discovered_agents', {})
        role_details = agents.get('role_details', {})

        # Get active roles
        active_roles = []
        for role, details in role_details.items():
            if details.get('agents', []):
                active_roles.append(role)

        # Check which roles have B codes
        covered_roles = set()
        for code in b_codes:
            role = code.get("applies_to_role", "")
            if role:
                covered_roles.add(role)

        missing = [r for r in active_roles if r not in covered_roles]
        return missing

    def _check_b_overlaps(self, b_codes: List[Dict]) -> List[Dict]:
        """Detect potentially overlapping B codes using LLM analysis."""
        if len(b_codes) < 2:
            return []

        def summarize(codes: List[Dict]) -> List[Dict]:
            return [{
                "code": c.get("code", ""),
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
                "applies_to_role": c.get("applies_to_role", ""),
            } for c in codes if isinstance(c, dict)]

        prompt = f"""Analyze these Category B (role-specific quality failure) codes for semantic overlaps.

Two B codes OVERLAP if they describe the same quality failure for the same role,
even if worded differently. Codes for DIFFERENT roles cannot overlap (e.g.,
Solver_Wrong_Approach and Checker_Wrong_Criteria are distinct even if similar).

Also flag any B code that actually describes a SYSTEM failure (empty output,
no response, timeout, crash) — these belong in Category A, not B.

B CODES:
{json.dumps(summarize(b_codes), indent=2)}

For each issue, explain why and recommend a fix.
If no overlaps or misclassifications, return an empty list.

OUTPUT JSON:
{{
  "overlaps": [
    {{
      "code1": "B.X",
      "code2": "B.Y",
      "reason": "Both describe the same quality failure: ...",
      "recommendation": "Keep B.X, merge B.Y into it"
    }}
  ]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)
            return result.get("overlaps", [])
        except Exception as e:
            progress(f"  [!] B overlap check error: {e}")
            return []

    def _fix_b_coverage_and_overlaps(self, b_codes: List[Dict],
                                      coverage_gaps: List[str],
                                      overlaps: List[Dict]) -> List[Dict]:
        """Fix B-code coverage gaps (missing roles) and resolve overlaps."""
        agents = self.structure_info.get('discovered_agents', {})
        role_details = agents.get('role_details', {})

        # Build context about each missing role
        gap_section = ""
        if coverage_gaps:
            role_info_parts = []
            for role in coverage_gaps:
                details = role_details.get(role, {})
                role_agents = details.get('agents', [])
                role_definition = details.get('definition', 'N/A')
                role_info_parts.append(
                    f"  - {role}: {role_definition} "
                    f"(agents: {', '.join(role_agents[:5]) if role_agents else 'unknown'})"
                )
            gap_section = f"""
MISSING ROLE COVERAGE — these roles have agents but NO B codes:
{chr(10).join(role_info_parts)}

Generate B codes for each missing role. Each role needs at least 2 codes covering
distinct quality failures relevant to that role's purpose.

CRITICAL: B codes are about QUALITY of work done, NOT about system failures.
- GOOD: Solver_Wrong_Approach, Checker_Superficial_Verification
- BAD: Solver_No_Output (this is a system failure → Category A)"""

        overlap_section = ""
        if overlaps:
            overlap_section = f"""
OVERLAPPING CODES — merge these:
{json.dumps(overlaps, indent=2)}

For each overlap, merge the two codes into one stronger code. Keep the better
name and combine the detection heuristics."""

        # Format current codes for context
        def summarize(codes: List[Dict]) -> List[Dict]:
            return [{
                "code": c.get("code", ""),
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
                "applies_to_role": c.get("applies_to_role", ""),
            } for c in codes if isinstance(c, dict)]

        prompt = f"""Fix Category B codes: fill role coverage gaps and resolve overlaps.

CURRENT B CODES:
{json.dumps(summarize(b_codes), indent=2)}

{gap_section}
{overlap_section}

NAMING RULE: B-code names MUST start with the role type ({', '.join(r.capitalize() + '_' for r in role_details.keys())})
A/B BOUNDARY: B codes describe quality of work ONLY. Never describe system failures (no output, timeout, crash).

Return the COMPLETE updated list of B codes (existing + new, with overlaps merged).

OUTPUT JSON:
{{
  "codes": [
    {{
      "code": "B.X",
      "name": "Role_Descriptive_Name",
      "definition": "Concise definition about quality of work.",
      "when_to_use": "When to apply",
      "when_not_to_use": "When NOT to apply",
      "detection_heuristics": ["observable quality signal", "..."],
      "severity": "critical|major|minor",
      "applies_to_role": "one of the active roles"
    }}
  ],
  "changes_made": ["Added RoleName_X for ...", "Merged B.2 and B.5 into ..."]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)

            new_codes = result.get("codes", [])
            changes = result.get("changes_made", [])

            if new_codes and isinstance(new_codes, list):
                valid = [c for c in new_codes if isinstance(c, dict)]
                if valid:
                    progress(f"    B-code changes: {changes[:5]}")
                    return normalize_code_ids(valid, "B")

            return b_codes

        except Exception as e:
            progress(f"  [!] B coverage/overlap fix error: {e}")
            return b_codes

    def _check_c_code(self, code: Dict) -> List[str]:
        """Check C code against rules."""
        issues = []
        name = code.get("name", "").lower()

        for role_type in self.role_types:
            if role_type in name:
                issues.append(f"Name contains role type '{role_type}' (not allowed for C codes)")
                break

        definition = code.get("definition", "")
        if not definition or len(definition) < 10:
            issues.append("Missing or too short definition")

        heuristics = code.get("detection_heuristics", [])
        if not heuristics:
            issues.append("Missing detection heuristics")

        if not code.get("when_to_use"):
            issues.append("Missing when_to_use")
        if not code.get("when_not_to_use"):
            issues.append("Missing when_not_to_use")

        return issues

    def _check_c_coverage(self, c_codes: List[Dict]) -> List[str]:
        """Check that C codes cover reasoning failures across all subdomains.

        Uses subdomains from domain_info to verify each has at least one
        relevant C code. Returns list of uncovered subdomains.
        """
        subdomains = self.domain_info.get('subdomains', [])
        if not subdomains:
            return []

        # Build combined text from all C code names + definitions + heuristics
        all_text = ""
        for code in c_codes:
            all_text += " " + code.get("name", "").lower()
            all_text += " " + code.get("definition", "").lower()
            all_text += " " + code.get("when_to_use", "").lower()
            for h in code.get("detection_heuristics", []):
                if isinstance(h, str):
                    all_text += " " + h.lower()

        # Subdomain-specific keywords to look for
        # These are generic enough to work across domains — the subdomain name
        # itself plus common associated terms
        gaps = []
        for subdomain in subdomains:
            subdomain_lower = subdomain.lower()
            # Check if the subdomain name or closely related terms appear
            if subdomain_lower not in all_text:
                # Also check for common related terms
                related_found = False
                # Build related terms from the subdomain name
                subdomain_words = subdomain_lower.split()
                for word in subdomain_words:
                    if len(word) > 3 and word in all_text:
                        related_found = True
                        break
                if not related_found:
                    gaps.append(subdomain)

        return gaps

    def _check_c_overlaps(self, c_codes: List[Dict]) -> List[Dict]:
        """Detect potentially overlapping C codes using LLM analysis."""
        if len(c_codes) < 2:
            return []

        def summarize(codes: List[Dict]) -> List[Dict]:
            return [{
                "code": c.get("code", ""),
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
            } for c in codes if isinstance(c, dict)]

        prompt = f"""Analyze these Category C (Domain Reasoning Failure) codes for semantic overlaps.

Two codes OVERLAP if a judge reading a trace could not reliably distinguish between them.
The test: can you describe a concrete scenario where code X applies but code Y does NOT?
If you cannot, they overlap.

C CODES:
{json.dumps(summarize(c_codes), indent=2)}

For each overlap pair, explain why they overlap and which should be kept.
If no overlaps exist, return an empty list.

OUTPUT JSON:
{{
  "overlaps": [
    {{
      "code1": "C.X",
      "code2": "C.Y",
      "reason": "Both describe the same reasoning flaw: ...",
      "recommendation": "Keep C.X, merge C.Y into it"
    }}
  ]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)
            return result.get("overlaps", [])
        except Exception as e:
            progress(f"  [!] C overlap check error: {e}")
            return []

    def _fix_c_overlaps(self, c_codes: List[Dict], overlaps: List[Dict]) -> List[Dict]:
        """Resolve C-code overlaps by merging."""
        if not overlaps:
            return c_codes

        domain = self.domain_info.get('domain', {})
        domain_name = domain.get('name', 'Unknown')

        def summarize(codes: List[Dict]) -> List[Dict]:
            return [{
                "code": c.get("code", ""),
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
            } for c in codes if isinstance(c, dict)]

        prompt = f"""Resolve these overlaps in Category C (Domain Reasoning Failure) codes.

Domain: {domain_name}

CURRENT C CODES:
{json.dumps(summarize(c_codes), indent=2)}

OVERLAPS DETECTED:
{json.dumps(overlaps, indent=2)}

Merge overlapping codes. For each merged code, combine the best elements
of both definitions and heuristics. The result should have fewer, clearer codes.

RULES:
- C codes must NOT contain agent role names ({', '.join(r.capitalize() for r in self.role_types)})
- Each code must describe a reasoning flaw detectable from the trace alone
- Each code must be distinguishable from every other code

Return the NAMES of codes to keep (after merging). Use original names for unmerged codes
and the recommended keeper's name for merged pairs.

OUTPUT JSON:
{{
  "kept_codes": [
    {{"name": "...", "definition": "merged or original definition"}}
  ],
  "removed": [{{"name": "...", "reason": "Merged into ..."}}]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)

            kept_names = {c.get('name', '').lower() for c in result.get('kept_codes', [])}
            kept = [c for c in c_codes if c.get('name', '').lower() in kept_names]

            if not kept:
                progress(f"  [!] C overlap fix removed all codes, falling back")
                kept = c_codes

            return normalize_code_ids(kept, "C")
        except Exception as e:
            progress(f"  [!] C overlap fix error: {e}")
            return c_codes

    def _fix_c_coverage_and_overlaps(self, c_codes: List[Dict], coverage_gaps: List[str],
                                        overlaps: List[Dict]) -> List[Dict]:
        """Fix C-code coverage gaps and overlaps in one LLM call."""
        domain = self.domain_info.get('domain', {})
        domain_name = domain.get('name', 'Unknown')

        def summarize(codes: List[Dict]) -> List[Dict]:
            return [{
                "code": c.get("code", ""),
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
            } for c in codes if isinstance(c, dict)]

        gaps_str = ""
        if coverage_gaps:
            gaps_str = f"""
COVERAGE GAPS — these subdomains have NO C codes covering their characteristic reasoning failures:
{json.dumps(coverage_gaps, indent=2)}

For each gap, generate ONE new C code that captures the most common/important reasoning
failure type for that subdomain. The code must:
- Be detectable from the trace alone (no need to solve the problem)
- Not overlap with existing codes
- Follow all C-code naming rules (no role names)
"""

        overlaps_str = ""
        if overlaps:
            overlaps_str = f"""
OVERLAPS DETECTED — merge these:
{json.dumps(overlaps, indent=2)}
"""

        prompt = f"""Fix Category C (Domain Reasoning Failure) codes.

Domain: {domain_name}

CURRENT C CODES:
{json.dumps(summarize(c_codes), indent=2)}
{gaps_str}{overlaps_str}
Return the complete list of C codes after fixes. Include:
- All existing codes (unchanged unless merged)
- Merged codes (combining overlapping pairs)
- New codes for coverage gaps

Each code needs: code, name, definition, when_to_use, when_not_to_use,
detection_heuristics (list of strings), severity (critical/major/minor).

RULES:
- C codes must NOT contain agent role names
- Each code must describe a reasoning flaw detectable from the trace alone
- Each code must be distinguishable from every other code

OUTPUT JSON:
{{
  "codes": [
    {{
      "code": "C.N",
      "name": "...",
      "definition": "...",
      "when_to_use": "...",
      "when_not_to_use": "...",
      "detection_heuristics": ["..."],
      "severity": "major"
    }}
  ]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)
            new_codes = result.get("codes", [])

            if not new_codes:
                progress(f"  [!] C coverage/overlap fix returned no codes, falling back")
                return c_codes

            # For codes that match existing ones by name, preserve the original full object
            # and only use the LLM output for genuinely new codes
            existing_by_name = {c.get("name", "").lower(): c for c in c_codes}
            final_codes = []
            for nc in new_codes:
                nc_name = nc.get("name", "").lower()
                if nc_name in existing_by_name:
                    # Keep the original full code object
                    final_codes.append(existing_by_name[nc_name])
                else:
                    # New or merged code from LLM
                    final_codes.append(nc)

            return normalize_code_ids(final_codes, "C")
        except Exception as e:
            progress(f"  [!] C coverage/overlap fix error: {e}")
            return c_codes

    def _fix_codes(self, codes: List[Dict], issues_list: List[Dict], category: str) -> List[Dict]:
        """Fix codes with issues using LLM."""
        if not issues_list:
            return codes

        trace_format = self.structure_info.get('trace_format', {})
        key_fields = trace_format.get('key_fields', [])
        fields_str = ", ".join([f.get('field_name', f.get('field', '')) for f in key_fields[:5]]) if key_fields else "agent output, verdict, final answer"

        codes_to_fix = []
        for issue_info in issues_list:
            code_id = issue_info.get("code")
            for code in codes:
                if code.get("code") == code_id:
                    codes_to_fix.append({
                        "code": code,
                        "issues": issue_info.get("issues", [])
                    })
                    break

        if not codes_to_fix:
            return codes

        prompt = f"""Fix these Category {category} codes that have validation issues.

CATEGORY {category} RULES:
{f"- Names must NOT contain role types ({', '.join(r.capitalize() for r in self.role_types)})" if category in ["A", "C"] else f"- Names MUST contain role type ({', '.join(r.capitalize() + '_' for r in self.role_types)})"}
{"- Must have applies_to_role field" if category == "B" else ""}
- Definition: concise and clear
- detection_heuristics: required (as many as needed for clarity)
- when_to_use and when_not_to_use: required

TRACE FIELDS TO REFERENCE: {fields_str}

CODES TO FIX:
{json.dumps(codes_to_fix, indent=2)}

Fix all issues. Keep code IDs the same.

OUTPUT JSON:
{{
  "fixed_codes": [...]
}}"""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)
            fixed_codes = result.get('fixed_codes', [])

            fixed_ids = {}
            for c in fixed_codes:
                # LLM may return several formats:
                # 1. Wrapped with issues: {"code": {...}, "issues": [...]}
                # 2. Hybrid: {"code": {"code": "A.1", "name": "...", ...}, "detection_heuristics": [...], ...}
                # 3. Proper code object: {"code": "A.1", "name": "...", ...}

                if isinstance(c.get('code'), dict):
                    # The "code" field is a nested dict - merge it with top-level fields
                    inner_code = c.get('code')
                    actual_code = inner_code.copy()
                    # Copy over any extra fields from top level (detection_heuristics, when_to_use, etc.)
                    for key, val in c.items():
                        if key not in ('code', 'issues') and key not in actual_code:
                            actual_code[key] = val
                else:
                    # Already a proper code object with code as string
                    actual_code = c.copy()
                    # Remove issues if present (not part of code schema)
                    actual_code.pop('issues', None)

                cid = actual_code.get('code')
                # LLM may return the code field as a nested dict; extract string ID
                if isinstance(cid, dict):
                    cid = cid.get('code', str(cid))
                if isinstance(cid, str):
                    fixed_ids[cid] = actual_code

            updated = []
            for code in codes:
                code_id = code.get('code')
                if code_id in fixed_ids:
                    updated.append(fixed_ids[code_id])
                else:
                    updated.append(code)

            progress(f"    Fixed {len(fixed_codes)} {category} codes")
            return updated

        except Exception as e:
            progress(f"    [!] Fix error: {e}")
            return codes


# =============================================================================
# MAIN PIPELINE
# =============================================================================

class LLMNomos:
    """LLM_Nomos v13.0 - Architectural Risk Analysis + Behavioral Signal Extraction"""

    def __init__(
        self,
        traces_dir: Path,
        output_dir: Path,
        client: TextProvider | None = None,
    ):
        self.client = client or create_provider(Config.PROVIDER, Config.MODEL)
        self.traces_dir = traces_dir
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.traces: List[Dict] = []
        self.domain_info: Dict = {}
        self.structure_info: Dict = {}
        self.trace_signals: Dict = {}

    def run(self) -> Dict:
        progress("=" * 70)
        progress("LLM_Nomos v13.0 - Architectural Risk + Behavioral Signals")
        progress("=" * 70)
        progress(f"Model: {Config.MODEL}")

        self._load_traces()

        if not self.traces:
            progress("ERROR: No traces found!")
            return {}

        progress(f"Loaded {len(self.traces)} traces")

        # Step 1: System & Domain Analysis
        progress("\n" + "=" * 50)
        domain_analyzer = SystemDomainAnalyzer(self.client)
        self.domain_info = domain_analyzer.analyze(self.traces)
        self._save_step("step1_domain_info", self.domain_info)

        # Step 2: Trace Structure Extraction
        progress("\n" + "=" * 50)
        structure_extractor = TraceStructureExtractor(self.client)
        self.structure_info = structure_extractor.extract(self.traces)
        self._save_step("step2_structure_info", self.structure_info)

        # Step 2.5: Extract behavioral signals from ALL traces (for A-code generation)
        progress("\n" + "=" * 50)
        progress("Step 2.5: Trace Signal Extraction")
        signal_extractor = TraceSignalExtractor()
        self.trace_signals = signal_extractor.extract_signals(self.traces)
        self._save_step("step2_5_trace_signals", self.trace_signals)

        # Step 3: Generate Category A (architectural risk + empirical behavioral)
        progress("\n" + "=" * 50)
        a_gen = CategoryGenerator(self.client, "A", self.domain_info, self.structure_info,
                                  trace_signals=self.trace_signals)
        a_codes = a_gen.generate(self.traces)
        self._save_step("step3_a_codes", {"codes": a_codes})

        # Step 4: Generate Category B (fully generated: role guidance + capabilities)
        progress("\n" + "=" * 50)
        b_gen = CategoryGenerator(self.client, "B", self.domain_info, self.structure_info)
        b_codes = b_gen.generate(self.traces, {"category_a": a_codes})
        self._save_step("step4_b_codes", {"codes": b_codes})

        # Step 5: Generate Category C (domain reasoning failures - two stage)
        progress("\n" + "=" * 50)
        c_gen = CategoryGenerator(self.client, "C", self.domain_info, self.structure_info)
        c_codes = c_gen.generate(self.traces, {"category_a": a_codes, "category_b": b_codes})
        self._save_step("step5_c_codes", {"codes": c_codes})

        # Step 6: Cross-Category Deduplication
        progress("\n" + "=" * 50)
        dedup = CrossCategoryDeduplicator(self.client)
        dedup_result = dedup.deduplicate(a_codes, b_codes, c_codes)
        self._save_step("step6_dedup", dedup_result)

        # Step 6.5: Max-codes cap (if configured)
        if Config.MAX_CODES > 0:
            all_codes = (
                dedup_result.get("category_a", [])
                + dedup_result.get("category_b", [])
                + dedup_result.get("category_c", [])
            )
            total = len(all_codes)
            if total > Config.MAX_CODES:
                progress(f"\nStep 6.5: Max-codes cap ({total} -> {Config.MAX_CODES})")
                dedup_result = self._apply_max_codes_cap(
                    dedup_result, Config.MAX_CODES
                )
                self._save_step("step6_5_capped", dedup_result)

        # Step 7: Cross-Category Validation
        progress("\n" + "=" * 50)
        validator = CrossCategoryValidator(self.client, self.structure_info)
        validated = validator.validate(
            dedup_result.get("category_a", []),
            dedup_result.get("category_b", []),
            dedup_result.get("category_c", [])
        )
        self._save_step("step7_validated", validated)

        # Step 8: Final Taxonomy Check
        progress("\n" + "=" * 50)
        checker = TaxonomyChecker(self.client, self.structure_info, self.domain_info)
        final = checker.check_and_fix(
            validated.get("category_a", []),
            validated.get("category_b", []),
            validated.get("category_c", [])
        )
        self._save_step("step8_final", final)

        # Build final taxonomy
        taxonomy = self._build_taxonomy(final)

        # Save
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.output_dir / f"taxonomy_v13.0_{timestamp}.json"
        save_json(taxonomy, output_path)

        self._print_summary(taxonomy)

        return taxonomy

    def _load_traces(self):
        progress("Loading traces...")
        self.traces = load_traces(self.traces_dir)
        progress(f"  Successfully normalized {len(self.traces)} traces")

    def _is_tau_bench_trace(self, item: Dict) -> bool:
        """Check if a trace item is in tau_bench format."""
        return (isinstance(item, dict)
                and 'traj' in item
                and 'task_id' in item
                and 'reward' in item
                and isinstance(item.get('traj'), list))

    def _convert_tau_bench_trace(self, item: Dict, index: int, file_stem: str) -> Dict:
        """Convert a tau_bench trace to standard format."""
        task_id = item.get('task_id', index)
        trial = item.get('trial', 0)
        reward = item.get('reward', 0.0)
        info = item.get('info', {})
        task_info = info.get('task', {})
        traj = item.get('traj', [])

        # Extract task instruction
        task = task_info.get('instruction', '')

        # Determine domain from filename
        domain = 'unknown'
        file_lower = file_stem.lower()
        if 'airline' in file_lower:
            domain = 'airline'
        elif 'retail' in file_lower:
            domain = 'retail'

        # Determine LLM from filename
        llm_name = 'unknown'
        if 'gpt-4o' in file_lower:
            llm_name = 'gpt-4o'
        elif 'sonnet' in file_lower:
            llm_name = 'claude-3.5-sonnet'

        # Build readable trajectory from traj messages
        trajectory_parts = []
        for msg in traj:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            tool_calls = msg.get('tool_calls') or []

            if role == 'system':
                trajectory_parts.append(f"[SYSTEM]\n{content}")
            elif role == 'user':
                trajectory_parts.append(f"[USER]\n{content}")
            elif role == 'assistant':
                if content:
                    trajectory_parts.append(f"[ASSISTANT]\n{content}")
                if tool_calls:
                    for tc in tool_calls:
                        func = tc.get('function', {})
                        fn_name = func.get('name', 'unknown')
                        fn_args = func.get('arguments', '{}')
                        trajectory_parts.append(f"[ASSISTANT TOOL CALL] {fn_name}({fn_args})")
            elif role == 'tool':
                tool_name = msg.get('name', 'unknown')
                trajectory_parts.append(f"[TOOL RESPONSE: {tool_name}]\n{content}")

        trajectory_parts.append(f"\n=== RESULT: {'SUCCESS' if reward == 1.0 else 'FAILURE'} (reward={reward}) ===")

        problem_id = f"tau_bench_{domain}_{task_id}_trial{trial}"

        return {
            'problem_id': problem_id,
            'task': task[:500] if task else '',
            'raw_trajectory': '\n\n'.join(trajectory_parts),
            'metadata': {
                'mas_name': 'tau_bench',
                'llm_name': llm_name,
                'benchmark_name': domain,
                'trace_id': f"{task_id}_trial{trial}",
                'reward': reward,
                'trial': trial,
                'task_id': task_id,
                '_format': 'tau_bench'
            }
        }

    def _convert_codex_session(self, entries: List[Dict], file_path: Path) -> Optional[Dict]:
        """Convert a Codex CLI session.jsonl into a standard trace."""
        task = ""
        llm_name = "unknown"
        trajectory_parts = []

        # Determine model from parent directory name or turn_context
        parent_name = file_path.parent.name.lower()

        for entry in entries:
            entry_type = entry.get('type', '')
            payload = entry.get('payload', {})

            if entry_type == 'session_meta':
                # Extract task from instructions (first 500 chars)
                instructions = payload.get('instructions', '')
                # Pull the core task description
                task_match = re.search(r'\*\*Task\*\*:\s*\n(.*?)(?:\n====|\n---|\n\*\*)', instructions, re.DOTALL)
                if task_match:
                    task = task_match.group(1).strip()[:500]
                elif instructions:
                    task = instructions[:500]
                llm_name = payload.get('model_provider', llm_name)

            elif entry_type == 'turn_context':
                model = payload.get('model', '')
                if model:
                    llm_name = model

            elif entry_type == 'response_item':
                sub_type = payload.get('type', '')

                if sub_type == 'message':
                    role = payload.get('role', 'unknown')
                    content_parts = payload.get('content', [])
                    if isinstance(content_parts, list):
                        text = '\n'.join(
                            p.get('text', '') for p in content_parts
                            if isinstance(p, dict) and p.get('text')
                        )
                    elif isinstance(content_parts, str):
                        text = content_parts
                    else:
                        text = str(content_parts)
                    if text:
                        trajectory_parts.append(f"[{role.upper()}]\n{text[:2000]}")

                elif sub_type == 'function_call':
                    fn_name = payload.get('name', 'unknown')
                    fn_args = payload.get('arguments', '{}')
                    # Truncate long args (tool outputs can be huge)
                    if len(fn_args) > 500:
                        fn_args = fn_args[:500] + '...'
                    trajectory_parts.append(f"[TOOL CALL] {fn_name}({fn_args})")

                elif sub_type == 'function_call_output':
                    output = payload.get('output', '')
                    if isinstance(output, str) and len(output) > 1000:
                        output = output[:1000] + '...'
                    trajectory_parts.append(f"[TOOL OUTPUT]\n{output}")

        if not trajectory_parts:
            return None

        # Build problem_id from filename: e.g. "Scenario-1_run1"
        problem_id = f"codex_{parent_name}_{file_path.stem}"

        return {
            'problem_id': problem_id,
            'task': task,
            'raw_trajectory': '\n\n'.join(trajectory_parts),
            'metadata': {
                'mas_name': 'codex_session',
                'llm_name': llm_name,
                'benchmark_name': 'sre',
                'trace_id': file_path.stem,
                '_format': 'codex_session'
            }
        }

    def _load_single_file(self, file_path: Path):
        """Load traces from a single file."""
        try:
            content = file_path.read_text(encoding='utf-8')

            try:
                data = json.loads(content)

                if isinstance(data, list):
                    # Check if this is a tau_bench file (list of tau_bench traces)
                    if len(data) > 0 and self._is_tau_bench_trace(data[0]):
                        for i, item in enumerate(data):
                            trace = self._convert_tau_bench_trace(item, i, file_path.stem)
                            if trace:
                                self.traces.append(trace)
                        return

                    for i, item in enumerate(data):
                        trace = self._convert_trace(item, i)
                        if trace:
                            trace['problem_id'] = trace.get('problem_id', f"{file_path.stem}_{i}")
                            self.traces.append(trace)
                    return

                elif isinstance(data, dict):
                    # Check if single tau_bench trace
                    if self._is_tau_bench_trace(data):
                        trace = self._convert_tau_bench_trace(data, 0, file_path.stem)
                        if trace:
                            self.traces.append(trace)
                        return

                    trace = self._convert_trace(data, 0)
                    if trace:
                        trace['problem_id'] = trace.get('problem_id', file_path.stem)
                        self.traces.append(trace)
                    return

            except json.JSONDecodeError:
                pass

            # JSONL format
            lines = content.strip().replace('\r\n', '\n').split('\n')
            events = []
            is_event_log = False
            is_codex_session = False
            codex_entries = []

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if isinstance(data, dict):
                        # Detect Codex CLI session format
                        if data.get('type') in ('session_meta', 'response_item', 'turn_context', 'event_msg'):
                            is_codex_session = True
                            codex_entries.append(data)
                        elif 'event' in data:
                            is_event_log = True
                            events.append(data)
                        else:
                            trace = self._convert_trace(data, len(self.traces))
                            if trace:
                                trace['problem_id'] = trace.get('problem_id', file_path.stem)
                                self.traces.append(trace)
                except json.JSONDecodeError:
                    continue

            if is_codex_session and codex_entries:
                trace = self._convert_codex_session(codex_entries, file_path)
                if trace:
                    self.traces.append(trace)
            elif is_event_log and events:
                trace = self._reconstruct_trace_from_events(events, file_path.stem)
                if trace:
                    self.traces.append(trace)

        except Exception as e:
            progress(f"  [!] Error loading {file_path.name}: {e}")

    def _reconstruct_trace_from_events(self, events: List[Dict], file_stem: str) -> Optional[Dict]:
        """Reconstruct a trace from event log format."""
        problem_id = file_stem
        task = ""
        trajectory_parts = []
        agents_seen = set()
        metadata = {}

        for event in events:
            event_type = event.get('event', '')
            agent = event.get('agent', '')
            data = event.get('data', {})

            if agent:
                agents_seen.add(agent)

            if event_type == 'run_start':
                task = event.get('task', '')
                meta = event.get('meta', {})
                problem_id = event.get('problem_id', file_stem)
                metadata['model'] = meta.get('model', '')

            elif event_type == 'prompt_sent':
                system_prompt = data.get('system', '')
                user_prompt = data.get('user', '')
                if agent:
                    trajectory_parts.append(f"\n=== {agent.upper()} ===")
                if system_prompt:
                    trajectory_parts.append(f"[System] {system_prompt[:500]}")
                if user_prompt:
                    trajectory_parts.append(f"[User] {user_prompt[:1000]}")

            elif event_type == 'response_received':
                response = data.get('response', data.get('content', ''))
                if response:
                    trajectory_parts.append(f"[Response] {response}")

            elif event_type == 'agent_output':
                output = data.get('output', data.get('content', str(data)))
                if agent:
                    trajectory_parts.append(f"\n=== {agent.upper()} OUTPUT ===")
                trajectory_parts.append(str(output)[:2000])

            elif event_type in ['run_end', 'run_complete', 'final_answer']:
                answer = data.get('answer', data.get('final_answer', data.get('result', '')))
                if answer:
                    trajectory_parts.append(f"\n=== FINAL_ANSWER ===\n{answer}")

        if not trajectory_parts:
            return None

        return {
            'problem_id': str(problem_id),
            'task': task,
            'raw_trajectory': '\n'.join(trajectory_parts),
            'metadata': {
                'mas_name': 'event_log',
                'llm_name': metadata.get('model', 'unknown'),
                'benchmark_name': 'math',
                'trace_id': problem_id,
                'agents_seen': list(agents_seen)
            }
        }

    def _convert_trace(self, item: Dict, index: int) -> Dict:
        """Convert various trace formats to standard format.

        If the item is already in Nomos format (has 'raw_trajectory' and nested
        'metadata' with 'agents_seen'), preserve those fields instead of
        reconstructing from top-level keys that don't exist in that format.
        """
        # Detect pre-formatted Nomos traces (produced by taxonomy_manager
        # trace formatters). These have metadata nested under a 'metadata' key
        # and 'raw_trajectory' / 'task' / 'problem_id' at the top level.
        existing_meta = item.get('metadata', {})
        if isinstance(existing_meta, dict) and existing_meta.get('agents_seen'):
            # Already in Nomos format — preserve original fields
            trajectory = item.get('raw_trajectory', '')
            return {
                'problem_id': item.get('problem_id', f"trace_{index}"),
                'task': item.get('task', ''),
                'raw_trajectory': trajectory,
                'metadata': existing_meta,
            }

        # Legacy / external trace formats — reconstruct from top-level keys
        mas_name = item.get('mas_name', 'unknown')
        llm_name = item.get('llm_name', 'unknown')
        benchmark = item.get('benchmark_name', 'unknown')
        trace_id = item.get('trace_id', index)

        trace_data = item.get('trace', {})
        trajectory = trace_data.get('trajectory', '') if isinstance(trace_data, dict) else ''

        if not trajectory:
            trajectory = item.get('raw_trajectory', '')

        problem_id = f"{mas_name}_{benchmark}_{trace_id}"

        task = ""
        task_match = re.search(r'task_prompt.*?[:|]\s*(.*?)(?:\n\*\*|\n\n|\n\|)', trajectory, re.IGNORECASE | re.DOTALL)
        if task_match:
            task = task_match.group(1).strip()[:500]

        return {
            'problem_id': problem_id,
            'task': task,
            'raw_trajectory': trajectory,
            'metadata': {
                'mas_name': mas_name,
                'llm_name': llm_name,
                'benchmark_name': benchmark,
                'trace_id': trace_id,
                'mast_annotation': item.get('mast_annotation', {})
            }
        }

    def _save_step(self, name: str, data: Dict):
        path = self.output_dir / f"{name}.json"
        save_json(data, path)

    def _apply_max_codes_cap(self, dedup_result: Dict, max_codes: int) -> Dict:
        """Use LLM to rank and keep only the top N most important codes."""
        all_codes = (
            dedup_result.get("category_a", [])
            + dedup_result.get("category_b", [])
            + dedup_result.get("category_c", [])
        )
        summaries = []
        for c in all_codes:
            code_id = c.get("code", "")
            name = str(c.get("name", ""))[:60]
            defn = str(c.get("definition", ""))[:120]
            summaries.append(f"{code_id}: {name} -- {defn}")
        summary_block = "\n".join(summaries)

        prompt = f"""You are evaluating a failure-mode taxonomy for single-agent C++ code generation.
There are {len(all_codes)} codes but we need to keep only the {max_codes} most important ones.

Rank these failure modes by importance for evaluating single-agent C++ competitive programming solutions.
Prioritize codes that:
1. Represent distinct, commonly-occurring failure modes
2. Provide actionable signal to improve code generation
3. Cover different categories (A=system, B=role-quality, C=domain-reasoning)

CODES:
{summary_block}

Return ONLY a JSON object:
{{"keep": ["A.1", "B.2", ...]}}

List exactly {max_codes} code IDs to keep, ordered by importance."""

        try:
            response = call_llm(self.client, prompt)
            result = extract_json(response)
            keep_ids = set(result.get("keep", [])[:max_codes])
            if len(keep_ids) < max_codes:
                # LLM returned too few; keep what it said + fill from original order
                for c in all_codes:
                    if len(keep_ids) >= max_codes:
                        break
                    keep_ids.add(c.get("code", ""))
        except Exception as e:
            progress(f"  [!] Max-codes cap LLM failed: {e}. Keeping first {max_codes} codes.")
            keep_ids = set(c.get("code", "") for c in all_codes[:max_codes])

        filtered_a = [c for c in dedup_result.get("category_a", []) if c.get("code") in keep_ids]
        filtered_b = [c for c in dedup_result.get("category_b", []) if c.get("code") in keep_ids]
        filtered_c = [c for c in dedup_result.get("category_c", []) if c.get("code") in keep_ids]

        kept = len(filtered_a) + len(filtered_b) + len(filtered_c)
        progress(f"  Kept {kept} codes (A={len(filtered_a)}, B={len(filtered_b)}, C={len(filtered_c)})")

        return {
            "category_a": filtered_a,
            "category_b": filtered_b,
            "category_c": filtered_c,
            "duplicates_found": dedup_result.get("duplicates_found", []),
            "max_codes_applied": max_codes,
        }

    def _build_taxonomy(self, final_codes: Dict) -> Dict:
        a_list = normalize_code_ids(final_codes.get("category_a", []), "A")
        b_list = normalize_code_ids(final_codes.get("category_b", []), "B")
        c_list = normalize_code_ids(final_codes.get("category_c", []), "C")

        def compact(code: Dict) -> Dict:
            """Build compact annotation-layer entry for judges."""
            entry = {
                "code": code["code"],
                "name": code.get("name", ""),
                "definition": code.get("definition", ""),
            }
            if code.get("severity"):
                entry["severity"] = code["severity"]
            if code.get("applies_to_role"):
                entry["applies_to_role"] = code["applies_to_role"]
            return entry

        return {
            "metadata": {
                "version": "13.0",
                "timestamp": datetime.now().isoformat(),
                "model": Config.MODEL,
                "pipeline": "Architectural Risk + Behavioral Signals",
                "traces_analyzed": len(self.traces),
                "counts": {
                    "category_a": len(a_list),
                    "category_b": len(b_list),
                    "category_c": len(c_list),
                    "total": len(a_list) + len(b_list) + len(c_list)
                }
            },
            "category_definitions": {
                "A": "System failures - agent-independent (can happen to ANY agent)",
                "B": "Role-specific QUALITY failures - about correctness of work done",
                "C": "Domain reasoning failures - MUST NOT contain agent type"
            },
            "role_definitions": self.structure_info.get('discovered_agents', {}).get('role_details', {}),
            "generation_method": {
                "A": "Fully generated via architectural risk analysis + empirical behavioral signals",
                "B": "Fully generated via role guidance + architecture + capabilities context",
                "C": "Domain-seeded from error patterns + trace-grounded from observed reasoning flaws"
            },
            "annotation_layer": {
                "description": "Compact taxonomy for annotation judges — code, name, definition, severity only",
                "category_a": [compact(c) for c in a_list],
                "category_b": [compact(c) for c in b_list],
                "category_c": [compact(c) for c in c_list]
            },
            "full_layer": {
                "description": "Full taxonomy with heuristics, when_to_use, when_not_to_use — for pipeline validation",
                "trace_signals": self.trace_signals,
                "domain_info": self.domain_info,
                "trace_format": self.structure_info.get("trace_format", {}),
                "architecture": self.structure_info.get("architecture", {}),
                "discovered_agents": self.structure_info.get("discovered_agents", {}),
                "category_a": {c["code"]: c for c in a_list},
                "category_b": {c["code"]: c for c in b_list},
                "category_c": {c["code"]: c for c in c_list}
            }
        }

    def _print_summary(self, taxonomy: Dict):
        progress("\n" + "=" * 70)
        progress("TAXONOMY GENERATION COMPLETE")
        progress("=" * 70)

        counts = taxonomy["metadata"]["counts"]
        progress(f"\nCategory A (System):  {counts['category_a']} codes (architectural risk + empirical)")
        progress(f"Category B (Role):    {counts['category_b']} codes (role guidance + capabilities)")
        progress(f"Category C (Domain):  {counts['category_c']} codes (domain-seeded + trace-grounded)")
        progress(f"{'─' * 40}")
        progress(f"Total:                {counts['total']} codes")

        # Show discovered roles
        full_layer = taxonomy.get("full_layer", {})
        agents = full_layer.get("discovered_agents", {})
        role_details = agents.get("role_details", {})
        progress("\nDiscovered Roles:")
        for role, details in role_details.items():
            agent_list = details.get("agents", [])
            if agent_list:
                progress(f"  {role}: {len(agent_list)} agents")


# =============================================================================
# CLI
# =============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="LLM_Nomos v13.0")
    parser.add_argument("--traces", type=str, required=True, help="Path to traces")
    parser.add_argument("--output", type=str, required=True, help="Output directory")
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "google", "bedrock"],
        default=os.getenv("ADAMAST_PROVIDER"),
        help="Model API provider",
    )
    parser.add_argument("--model", type=str, default=None, help="Model to use")
    parser.add_argument("--max-codes", type=int, default=0,
                        help="Max total codes (0 = no cap, default: 0)")

    args = parser.parse_args()

    Config.PROVIDER = args.provider
    Config.MODEL = resolve_model(args.provider, args.model)
    if args.max_codes > 0:
        Config.MAX_CODES = args.max_codes

    nomos = LLMNomos(Path(args.traces), Path(args.output))
    nomos.run()


if __name__ == "__main__":
    main()
