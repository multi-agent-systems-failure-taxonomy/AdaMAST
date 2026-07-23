"""
MATRS v14.1 - Multi-Agent Taxonomy Refinement System
=====================================================

Key Changes in v14.1:
- Confusion pair tracking: detects codes that frequently cause disagreement
- Overlapping code detection: identifies semantically similar codes upfront
- Smart disambiguation: when disagreement involves overlapping codes, creates
  decision rules based on observable evidence instead of just picking one
- Confusion guidance injection: annotators are warned about frequently confused codes

Key Changes in v14.0:
- Multi-phase DELIBERATIVE annotation (not just independent + voting)
- Phase 1: Independent Error Discovery
- Phase 2: Error Reconciliation - annotators see what others found, deliberate
- Phase 3: High-Level Failure Typing - categorize into A/B/C before coding
- Phase 4: Code Assignment with validation
- Phase 5: Code Deliberation - multi-turn discussion

Error Type Definitions:
- Type A: HOW infrastructure failed (input/output/connection issues)
- Type B: HOW the agent's role failed (observable consequence, at most 1 per agent)
- Type C: WHY the reasoning failed (underlying logic flaw, multiple allowed)

An agent can have:
- Multiple Type A errors (truncation, timeout, etc.)
- Multiple Type B errors (but at most 1 B-code per agent when coding)
- Multiple Type C errors (calculation errors, logic flaws, etc.)
"""

import json
import os
import re
import hashlib
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Any, Tuple, Optional, Set

from adamast.llm.providers import TextProvider, create_provider, resolve_model
from adamast.core.trace_formats import load_traces


# =============================================================================
# CONFIGURATION
# =============================================================================

class Config:
    """Configuration constants."""
    # Single model for all operations
    PROVIDER = os.getenv("ADAMAST_PROVIDER", "")
    MODEL = os.getenv("ADAMAST_MODEL", "")

    # Pipeline settings
    MAX_ROUNDS = 5
    TRACES_PER_ROUND = 5

    # Annotator settings
    NUM_ANNOTATORS = 4  # Alpha, Beta, Gamma, Delta (all identical at start)

    # Error identification: need at least 2 annotators to propose for reconciliation
    ERROR_PROPOSAL_THRESHOLD = 2
    # After reconciliation: need majority to confirm
    ERROR_CONFIRMATION_THRESHOLD = 3

    # Targets
    KAPPA_TARGET = 0.75
    COVERAGE_FLOOR = 0.70

    # Early stopping
    NO_EARLY_STOP = False

    # Calibration
    CALIBRATION_TRACES = 5

    # Deliberation settings
    MAX_DELIBERATION_ROUNDS = 2  # Max back-and-forth in discussions


# =============================================================================
# ANNOTATOR IDENTIFIERS
# =============================================================================

ANNOTATOR_IDS = ["Alpha", "Beta", "Gamma", "Delta"]


# =============================================================================
# UTILITIES
# =============================================================================

def progress(msg: str):
    """Print timestamped progress message."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")


def save_json(data: Dict, path: Path):
    """Save dictionary to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    progress(f"Saved: {path}")


def extract_json(text: str) -> Dict:
    """Extract JSON from LLM response."""
    if not text:
        return {}

    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try to find JSON block
    patterns = [
        r'```json\s*(.*?)\s*```',
        r'```\s*(.*?)\s*```',
        r'\{[\s\S]*\}'
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1) if '```' in pattern else match.group(0))
            except (json.JSONDecodeError, TypeError):
                continue

    return {}


# =============================================================================
# LLM UTILITIES
# =============================================================================

def call_llm(client: TextProvider, prompt: str, timeout: int = 120) -> str:
    """Call the configured provider without changing prompt content."""
    try:
        return client.complete(prompt, response_format="json")
    except Exception as e:
        progress(f"  [!] LLM error: {e}")
        raise


# =============================================================================
# STRATIFIED TRACE SAMPLER
# =============================================================================

class StratifiedTraceSampler:
    """Sample traces with diversity to improve coverage."""

    def __init__(self, traces: List[Dict]):
        self.traces = traces
        self.used_indices: Set[int] = set()
        self._stratify_traces()

    def _stratify_traces(self):
        """Categorize traces into strata."""
        self.strata = {
            "accept": [],
            "reject": [],
            "incomplete": [],
            "infra_issues": []
        }

        for i, trace in enumerate(self.traces):
            verdict = self._get_verdict(trace).upper()

            if verdict == "ACCEPT":
                self.strata["accept"].append(i)
            elif verdict == "REJECT":
                self.strata["reject"].append(i)
            else:
                self.strata["incomplete"].append(i)

            if self._has_infra_issues(trace):
                self.strata["infra_issues"].append(i)

    def _get_verdict(self, trace: Dict) -> str:
        """Extract verdict from trace."""
        if 'final_verdict' in trace:
            return trace['final_verdict']

        # tau_bench: use reward field
        if 'reward' in trace:
            return 'ACCEPT' if trace['reward'] == 1.0 else 'REJECT'

        # Codex session: no built-in verdict, treat as incomplete
        if trace.get('_format') == 'codex_session':
            return 'INCOMPLETE'

        # MAD format: no built-in verdict, treat as incomplete
        if trace.get('_format') == 'mad':
            return 'INCOMPLETE'

        events = trace.get('events', [])
        for event in reversed(events):
            if isinstance(event, dict):
                if event.get('event') == 'run_end':
                    summary = event.get('summary', {})
                    if isinstance(summary, dict):
                        return summary.get('checker_verdict', 'INCOMPLETE')
                if event.get('event') == 'extracted':
                    data = event.get('data', {})
                    if isinstance(data, dict) and 'verdict' in data:
                        return data['verdict']

        return 'INCOMPLETE'

    def _has_infra_issues(self, trace: Dict) -> bool:
        """Check if trace has infrastructure issues."""
        # Codex session: check for empty trajectory
        if trace.get('_format') == 'codex_session':
            raw = trace.get('raw_trajectory', '')
            return not raw or len(raw.strip()) < 50

        # MAD format: check for empty trajectory
        if trace.get('_format') == 'mad':
            raw = trace.get('raw_trajectory', '')
            return not raw or len(raw.strip()) < 50

        # tau_bench: check traj messages for empty assistant responses
        if trace.get('_format') == 'tau_bench':
            traj = trace.get('traj', [])
            for msg in traj:
                if msg.get('role') == 'assistant':
                    content = msg.get('content', '')
                    tool_calls = msg.get('tool_calls') or []
                    # Empty if no content AND no tool calls
                    if (not content or content.strip() == '') and not tool_calls:
                        return True
            return False

        events = trace.get('events', [])
        for event in events:
            if isinstance(event, dict):
                if event.get('event') == 'completion_received':
                    data = event.get('data', {})
                    if isinstance(data, dict):
                        content = data.get('content', '')
                        if not content or content.strip() == '':
                            return True
                        if data.get('finish_reason') == 'length':
                            return True
        return False

    def sample(self, n: int, round_num: int = 1) -> List[Dict]:
        """Sample n traces with stratification."""
        import random

        strata_sizes = {k: len([i for i in v if i not in self.used_indices])
                        for k, v in self.strata.items()}

        total_available = sum(strata_sizes.values())
        if total_available == 0:
            self.used_indices = set()
            strata_sizes = {k: len(v) for k, v in self.strata.items()}

        sampled_indices = set()
        strata_order = ["reject", "incomplete", "infra_issues", "accept"]

        for stratum in strata_order:
            available = [i for i in self.strata[stratum]
                        if i not in self.used_indices and i not in sampled_indices]

            if not available:
                continue

            take = min(len(available), max(1, n // 4))
            random.seed(round_num * 1000 + hash(stratum))
            random.shuffle(available)
            sampled_indices.update(available[:take])

            if len(sampled_indices) >= n:
                break

        if len(sampled_indices) < n:
            remaining = [i for i in range(len(self.traces))
                        if i not in self.used_indices and i not in sampled_indices]
            random.seed(round_num)
            random.shuffle(remaining)
            sampled_indices.update(remaining[:n - len(sampled_indices)])

        self.used_indices.update(sampled_indices)
        return [self.traces[i] for i in list(sampled_indices)[:n]]


# =============================================================================
# TRACE PARSER
# =============================================================================

class TraceParser:
    """Parse JSONL traces into structured agent outputs."""

    def parse(self, trace: Dict) -> Dict:
        """Parse a trace into structured format."""
        if not isinstance(trace, dict):
            return self._empty_parsed()

        # tau_bench format: parse traj message list
        if trace.get('_format') == 'tau_bench':
            return self._parse_tau_bench(trace)

        # Codex CLI session format
        if trace.get('_format') == 'codex_session':
            return self._parse_codex_session(trace)

        # MAD (Multi-Agent Dataset) format
        if trace.get('_format') == 'mad':
            return self._parse_mad_trace(trace)

        problem_id = trace.get('problem_id', 'unknown')
        events = trace.get('events', [])
        raw_trajectory = trace.get('raw_trajectory', '')

        agent_outputs = []
        final_verdict = "INCOMPLETE"
        final_answer = ""
        infrastructure_issues = []

        for event in events:
            if not isinstance(event, dict):
                continue

            event_type = event.get('event', '')
            agent = event.get('agent', '')

            if event_type == 'completion_received':
                data = event.get('data', {})
                content = data.get('content', '') if isinstance(data, dict) else ''

                if not content or content.strip() == '':
                    infrastructure_issues.append({
                        "type": "empty_output",
                        "agent": agent,
                        "description": f"{agent} produced empty output"
                    })

                finish_reason = data.get('finish_reason', '') if isinstance(data, dict) else ''
                if finish_reason == 'length':
                    infrastructure_issues.append({
                        "type": "truncated_output",
                        "agent": agent,
                        "description": f"{agent} output was truncated (hit token limit)"
                    })

                agent_outputs.append({
                    "agent": agent,
                    "role": event.get('role', ''),
                    "content": content[:2000] if content else "",
                    "status": "success" if content else "fail"
                })

            elif event_type == 'extracted':
                data = event.get('data', {})
                if isinstance(data, dict):
                    if 'verdict' in data:
                        final_verdict = data.get('verdict', 'INCOMPLETE')
                    if 'final_answer' in data:
                        final_answer = data.get('final_answer', '')

            elif event_type == 'run_end':
                summary = event.get('summary', {})
                if isinstance(summary, dict):
                    final_verdict = summary.get('checker_verdict', final_verdict)
                    final_answer = summary.get('final_answer', final_answer)

        if not agent_outputs and raw_trajectory:
            agent_outputs.append({
                "agent": "agent",
                "role": "solver",
                "content": raw_trajectory[:2000],
                "status": "success"
            })

        metadata = trace.get('metadata', {})
        outcome = metadata.get('outcome') if isinstance(metadata, dict) else None
        if isinstance(outcome, dict):
            status = str(outcome.get('status', '')).upper()
            if status in {'SUCCESS', 'PASSED', 'PASS', 'ACCEPT'}:
                final_verdict = 'ACCEPT'
            elif status in {'FAILURE', 'FAILED', 'FAIL', 'REJECT'}:
                final_verdict = 'REJECT'
        elif isinstance(outcome, str) and outcome:
            final_verdict = outcome.upper()

        return {
            "problem_id": problem_id,
            "agent_outputs": agent_outputs,
            "final_verdict": final_verdict,
            "final_answer": final_answer,
            "raw_trajectory": raw_trajectory,
            "infrastructure_issues": infrastructure_issues
        }

    def _parse_tau_bench(self, trace: Dict) -> Dict:
        """Parse a tau_bench trace into structured format."""
        problem_id = trace.get('problem_id', 'unknown')
        traj = trace.get('traj', [])
        reward = trace.get('reward', 0.0)
        raw_trajectory = trace.get('raw_trajectory', '')

        agent_outputs = []
        infrastructure_issues = []
        final_answer = ""

        # Collect all assistant turns as agent outputs
        for i, msg in enumerate(traj):
            role = msg.get('role', '')
            content = msg.get('content', '')
            tool_calls = msg.get('tool_calls') or []

            if role == 'assistant':
                # Build output content including tool calls
                output_parts = []
                if content:
                    output_parts.append(content)
                for tc in tool_calls:
                    func = tc.get('function', {})
                    fn_name = func.get('name', 'unknown')
                    fn_args = func.get('arguments', '{}')
                    output_parts.append(f"[Tool Call: {fn_name}({fn_args})]")

                combined = '\n'.join(output_parts)

                if not combined.strip():
                    infrastructure_issues.append({
                        "type": "empty_output",
                        "agent": "assistant",
                        "description": "Assistant produced empty output (no text and no tool calls)"
                    })

                agent_outputs.append({
                    "agent": "assistant",
                    "role": "solver",
                    "content": combined[:2000] if combined else "",
                    "status": "success" if combined.strip() else "fail"
                })

                # Track last assistant text as final answer
                if content:
                    final_answer = content

        final_verdict = 'ACCEPT' if reward == 1.0 else 'REJECT'

        return {
            "problem_id": problem_id,
            "agent_outputs": agent_outputs,
            "final_verdict": final_verdict,
            "final_answer": final_answer[:500] if final_answer else "",
            "raw_trajectory": raw_trajectory,
            "infrastructure_issues": infrastructure_issues
        }

    def _parse_mad_trace(self, trace: Dict) -> Dict:
        """Parse a MAD (Multi-Agent Dataset) trace into structured format."""
        problem_id = trace.get('problem_id', 'unknown')
        raw_trajectory = trace.get('raw_trajectory', '')
        metadata = trace.get('metadata', {})
        mas_name = metadata.get('mas_name', 'unknown')

        agent_outputs = []
        infrastructure_issues = []

        # Parse trajectory based on MAS type
        # Different MAS frameworks have different log formats
        if mas_name == 'ChatDev':
            # ChatDev uses [timestamp INFO] format with agent names
            pattern = r'\[[\d\-: ]+INFO\]\s*(?:System:\s*)?\*\*\[(\w+)\]\*\*.*?(?=\[[\d\-: ]+INFO\]|\Z)'
            matches = re.findall(pattern, raw_trajectory, re.DOTALL)
            for phase in matches[:20]:  # Limit to avoid huge outputs
                agent_outputs.append({
                    "agent": phase,
                    "role": "solver",
                    "content": f"Phase: {phase}",
                    "status": "success"
                })

        elif mas_name == 'MetaGPT':
            # MetaGPT uses [timestamp] FROM: X TO: Y format
            pattern = r'\[[\d\-: ]+\]\s*FROM:\s*(\w+)\s*TO:'
            matches = re.findall(pattern, raw_trajectory)
            seen_agents = set()
            for agent in matches:
                if agent not in seen_agents:
                    seen_agents.add(agent)
                    agent_outputs.append({
                        "agent": agent,
                        "role": "solver",
                        "content": f"Agent: {agent}",
                        "status": "success"
                    })

        elif mas_name in ('AG2', 'Magentic'):
            # AG2/Magentic use various formats, extract from trajectory
            pattern = r'(?:Response from|Message to)\s+(\w+(?:\s+\w+)?)\s*(?:Agent)?'
            matches = re.findall(pattern, raw_trajectory, re.IGNORECASE)
            seen_agents = set()
            for agent in matches[:20]:
                agent = agent.strip()
                if agent and agent not in seen_agents:
                    seen_agents.add(agent)
                    agent_outputs.append({
                        "agent": agent,
                        "role": "solver",
                        "content": f"Agent: {agent}",
                        "status": "success"
                    })

        else:
            # Generic parsing - just note that we have a trajectory
            if raw_trajectory:
                agent_outputs.append({
                    "agent": mas_name,
                    "role": "solver",
                    "content": raw_trajectory[:2000],
                    "status": "success"
                })

        # Check for infrastructure issues
        if not raw_trajectory or len(raw_trajectory.strip()) < 50:
            infrastructure_issues.append({
                "type": "empty_output",
                "agent": mas_name,
                "description": "Trace has minimal or no trajectory"
            })

        return {
            "problem_id": problem_id,
            "agent_outputs": agent_outputs,
            "final_verdict": "INCOMPLETE",
            "final_answer": "",
            "raw_trajectory": raw_trajectory,
            "infrastructure_issues": infrastructure_issues
        }

    def _parse_codex_session(self, trace: Dict) -> Dict:
        """Parse a Codex CLI session trace into structured format."""
        problem_id = trace.get('problem_id', 'unknown')
        raw_trajectory = trace.get('raw_trajectory', '')
        agent_outputs = []
        infrastructure_issues = []

        # Split raw trajectory back into parts to extract agent outputs
        parts = raw_trajectory.split('\n\n') if raw_trajectory else []
        last_assistant_text = ""

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if part.startswith('[ASSISTANT]') or part.startswith('[USER]'):
                role_end = part.index(']')
                role_tag = part[1:role_end].lower()
                text = part[role_end + 1:].strip()

                if role_tag == 'assistant':
                    agent_outputs.append({
                        "agent": "SRE Agent",
                        "role": "solver",
                        "content": text[:2000] if text else "",
                        "status": "success" if text else "fail"
                    })
                    if text:
                        last_assistant_text = text

                    if not text:
                        infrastructure_issues.append({
                            "type": "empty_output",
                            "agent": "SRE Agent",
                            "description": "SRE Agent produced empty message"
                        })

            elif part.startswith('[TOOL CALL]'):
                fn_info = part[len('[TOOL CALL]'):].strip()
                agent_outputs.append({
                    "agent": "SRE Agent",
                    "role": "solver",
                    "content": f"Tool call: {fn_info[:500]}",
                    "status": "success"
                })

            elif part.startswith('[TOOL OUTPUT]'):
                output = part[len('[TOOL OUTPUT]'):].strip()
                agent_outputs.append({
                    "agent": "SRE Agent (tool)",
                    "role": "tool",
                    "content": output[:2000] if output else "",
                    "status": "success" if output else "fail"
                })

        return {
            "problem_id": problem_id,
            "agent_outputs": agent_outputs,
            "final_verdict": "INCOMPLETE",
            "final_answer": last_assistant_text[:500] if last_assistant_text else "",
            "raw_trajectory": raw_trajectory,
            "infrastructure_issues": infrastructure_issues
        }

    def _empty_parsed(self) -> Dict:
        return {
            "problem_id": "unknown",
            "agent_outputs": [],
            "final_verdict": "INCOMPLETE",
            "final_answer": "",
            "raw_trajectory": "",
            "infrastructure_issues": []
        }


# =============================================================================
# SHARED KNOWLEDGE BASE
# =============================================================================

class SharedKnowledgeBase:
    """Shared knowledge accumulated across all annotators and rounds."""

    def __init__(self):
        self.learned_rules: List[Dict] = []
        self.anchor_examples: List[Dict] = []
        self.code_usage_stats: Counter = Counter()
        self.common_disagreements: List[Dict] = []
        self.taxonomy_clarifications: List[Dict] = []
        self._rule_hashes: Set[str] = set()

        # Confusion pair tracking for overlapping codes
        self.confusion_pairs: Counter = Counter()  # (code1, code2) -> disagreement count
        self.overlapping_codes: Dict[str, List[str]] = {}  # code -> similar codes
        self.disambiguation_rules: Dict[str, str] = {}  # "code1|code2" -> decision guidance
        self.default_codes: Dict[str, str] = {}  # symptom_key -> default code

    def _hash_rule(self, rule: Dict) -> str:
        """Create a hash for a rule to detect duplicates."""
        situation = rule.get('situation', '').lower().strip()
        code = rule.get('code', '').lower().strip()
        situation = re.sub(r'\s+', ' ', situation)
        situation = re.sub(r'\b(the|a|an|is|are|when|if)\b', '', situation)
        return hashlib.md5(f"{situation}|{code}".encode()).hexdigest()[:12]

    def add_rule(self, rule: Dict):
        """Add a learned rule with deduplication."""
        if not rule or not isinstance(rule, dict):
            return

        rule_hash = self._hash_rule(rule)
        if rule_hash in self._rule_hashes:
            return

        situation = rule.get('situation', '').lower()
        new_code = rule.get('code', '')

        for existing in self.learned_rules:
            existing_situation = existing.get('situation', '').lower()
            existing_code = existing.get('code', '')

            situation_words = set(situation.split())
            existing_words = set(existing_situation.split())
            overlap = len(situation_words & existing_words)

            if overlap >= 3 and new_code != existing_code:
                self.common_disagreements.append({
                    "type": "rule_conflict",
                    "existing_rule": existing,
                    "conflicting_rule": rule
                })
                return

        self._rule_hashes.add(rule_hash)
        self.learned_rules.append(rule)

    def consolidate_rules(self) -> int:
        """Consolidate rules by merging similar ones."""
        if len(self.learned_rules) < 2:
            return len(self.learned_rules)

        rules_by_code = defaultdict(list)
        for rule in self.learned_rules:
            code = rule.get('code', 'unknown')
            rules_by_code[code].append(rule)

        consolidated = []
        for code, rules in rules_by_code.items():
            if len(rules) == 1:
                consolidated.append(rules[0])
            else:
                merged = self._merge_rules(rules)
                consolidated.append(merged)

        self.learned_rules = consolidated
        self._rule_hashes = {self._hash_rule(r) for r in consolidated}

        return len(self.learned_rules)

    def _merge_rules(self, rules: List[Dict]) -> Dict:
        """Merge multiple rules for the same code."""
        if not rules:
            return {}
        if len(rules) == 1:
            return rules[0]

        situations = [r.get('situation', '') for r in rules if r.get('situation')]
        reasons = [r.get('reason', '') for r in rules if r.get('reason')]
        not_codes = [r.get('not_code', '') for r in rules if r.get('not_code')]

        return {
            "code": rules[0].get('code', ''),
            "situation": " OR ".join(set(situations[:3])),
            "reason": reasons[0] if reasons else "",
            "not_code": ", ".join(set(not_codes[:3])) if not_codes else "",
            "merged_from": len(rules)
        }

    def add_anchor(self, trace_id: str, agreed_errors: List[Dict],
                   agreed_codes: List[str], reasoning: str):
        """Add an anchor example."""
        self.anchor_examples.append({
            "trace_id": trace_id,
            "errors": agreed_errors,
            "codes": agreed_codes,
            "reasoning": reasoning,
            "round_added": len(self.anchor_examples) // Config.TRACES_PER_ROUND + 1
        })

    def record_code_usage(self, codes: List[str]):
        """Record code usage for statistics."""
        for code in codes:
            if isinstance(code, str):
                self.code_usage_stats[code] += 1

    def get_rules_for_prompt(self, max_rules: int = 10) -> str:
        """Get learned rules formatted for prompt injection."""
        if not self.learned_rules:
            return "No rules learned yet."

        recent_rules = self.learned_rules[-max_rules:]
        lines = []
        for i, rule in enumerate(recent_rules, 1):
            situation = rule.get('situation', '')
            code = rule.get('code', '')
            reason = rule.get('reason', '')
            not_code = rule.get('not_code', '')
            lines.append(f"{i}. When {situation} → Use {code} because {reason}" +
                        (f" (NOT {not_code})" if not_code else ""))

        return "\n".join(lines)

    def get_anchors_for_prompt(self, max_anchors: int = 5) -> str:
        """Get anchor examples formatted for prompt injection."""
        if not self.anchor_examples:
            return "No anchor examples yet."

        recent_anchors = self.anchor_examples[-max_anchors:]
        lines = []
        for anchor in recent_anchors:
            trace_id = anchor.get('trace_id', '?')
            codes = anchor.get('codes', [])
            reasoning = anchor.get('reasoning', '')[:200]
            lines.append(f"- Trace {trace_id}: Codes {codes}\n  Reasoning: {reasoning}")

        return "\n".join(lines)

    def record_confusion(self, code1: str, code2: str):
        """Record that two codes were confused (disagreed upon) for same error."""
        if code1 and code2 and code1 != code2:
            # Normalize order for consistent key
            pair = tuple(sorted([code1, code2]))
            self.confusion_pairs[pair] += 1

    def get_top_confusion_pairs(self, n: int = 5) -> List[Tuple[str, str, int]]:
        """Get the most frequently confused code pairs."""
        return [(p[0], p[1], count) for p, count in self.confusion_pairs.most_common(n)]

    def detect_overlapping_codes(self, taxonomy: Dict, client: TextProvider):
        """Analyze taxonomy to find codes with similar/overlapping definitions."""
        progress("  Detecting overlapping codes in taxonomy...")

        for cat in ['category_a', 'category_b', 'category_c']:
            codes = taxonomy.get(cat, {})
            if len(codes) < 2:
                continue

            # Build summary of codes
            code_summaries = []
            for code_id, code_data in codes.items():
                if isinstance(code_data, dict):
                    code_summaries.append({
                        "code": code_id,
                        "name": code_data.get('name', ''),
                        "definition": code_data.get('definition', '')[:150],
                        "heuristics": code_data.get('detection_heuristics', [])[:2]
                    })

            if len(code_summaries) < 2:
                continue

            prompt = f"""Analyze these taxonomy codes for SEMANTIC OVERLAP.
Find pairs of codes that describe SIMILAR SYMPTOMS or could be CONFUSED.

CODES:
{json.dumps(code_summaries, indent=2)}

Identify pairs where:
1. Same observable symptom could match both codes
2. Annotators would likely disagree between these codes
3. The codes differ only in inferred root cause, not observable evidence

OUTPUT JSON:
{{
  "overlapping_pairs": [
    {{
      "code1": "X.N",
      "code2": "X.M",
      "shared_symptom": "What symptom triggers both",
      "how_to_distinguish": "Observable evidence that differentiates them",
      "default_if_ambiguous": "X.N or X.M - which to use when can't distinguish"
    }}
  ]
}}"""

            response = call_llm(client, prompt)
            result = extract_json(response)

            pairs = result.get('overlapping_pairs', [])
            for pair in pairs:
                code1 = pair.get('code1', '')
                code2 = pair.get('code2', '')
                if code1 and code2:
                    # Record overlap relationship
                    if code1 not in self.overlapping_codes:
                        self.overlapping_codes[code1] = []
                    if code2 not in self.overlapping_codes[code1]:
                        self.overlapping_codes[code1].append(code2)

                    if code2 not in self.overlapping_codes:
                        self.overlapping_codes[code2] = []
                    if code1 not in self.overlapping_codes[code2]:
                        self.overlapping_codes[code2].append(code1)

                    # Store disambiguation rule
                    key = f"{min(code1,code2)}|{max(code1,code2)}"
                    self.disambiguation_rules[key] = pair.get('how_to_distinguish', '')

                    # Store default
                    default = pair.get('default_if_ambiguous', '')
                    if default:
                        symptom_key = pair.get('shared_symptom', '')[:50]
                        self.default_codes[symptom_key] = default

        overlap_count = sum(len(v) for v in self.overlapping_codes.values()) // 2
        progress(f"    Found {overlap_count} overlapping code pairs")

    def get_disambiguation_guidance(self, code1: str, code2: str) -> str:
        """Get guidance for distinguishing between two potentially confused codes."""
        key = f"{min(code1,code2)}|{max(code1,code2)}"
        return self.disambiguation_rules.get(key, '')

    def are_codes_overlapping(self, code1: str, code2: str) -> bool:
        """Check if two codes are known to overlap."""
        return code2 in self.overlapping_codes.get(code1, [])

    def get_default_code(self, codes: List[str]) -> Optional[str]:
        """If multiple overlapping codes, return the default one to use."""
        if len(codes) < 2:
            return None

        # Check if any pair is overlapping
        for i, c1 in enumerate(codes):
            for c2 in codes[i+1:]:
                if self.are_codes_overlapping(c1, c2):
                    # Find default for this pair
                    for symptom, default in self.default_codes.items():
                        if default in [c1, c2]:
                            return default
        return None

    def get_confusion_guidance_for_prompt(self) -> str:
        """Get confusion pair guidance for prompts."""
        if not self.confusion_pairs:
            return ""

        lines = ["FREQUENTLY CONFUSED CODE PAIRS (be careful to distinguish):"]
        for (c1, c2), count in self.confusion_pairs.most_common(5):
            guidance = self.get_disambiguation_guidance(c1, c2)
            lines.append(f"- {c1} vs {c2} (confused {count}x)")
            if guidance:
                lines.append(f"  How to distinguish: {guidance}")

        return "\n".join(lines)


# =============================================================================
# ANNOTATOR MEMORY
# =============================================================================

class AnnotatorMemory:
    """Persistent memory for a single annotator."""

    def __init__(self, annotator_id: str):
        self.annotator_id = annotator_id
        self.annotation_history: List[Dict] = []
        self.correction_log: List[Dict] = []
        self.my_common_codes: Counter = Counter()
        self.my_disagreements: List[Dict] = []
        self.round_summaries: List[str] = []

    def add_annotation(self, trace_id: str, phase: str, result: Dict):
        """Record an annotation I made."""
        self.annotation_history.append({
            "trace_id": trace_id,
            "phase": phase,
            "result": result,
            "timestamp": datetime.now().isoformat()
        })

        if phase == "code_assignment":
            codes = result.get('codes', [])
            for code in codes:
                if isinstance(code, str):
                    self.my_common_codes[code] += 1

    def add_correction(self, trace_id: str, my_answer: Any, correct_answer: Any,
                       reason: str):
        """Record when I was corrected."""
        self.correction_log.append({
            "trace_id": trace_id,
            "my_answer": my_answer,
            "correct_answer": correct_answer,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        })

    def add_disagreement(self, trace_id: str, my_view: Any, others_view: Any,
                         resolution: str):
        """Record a disagreement I was part of."""
        self.my_disagreements.append({
            "trace_id": trace_id,
            "my_view": my_view,
            "others_view": others_view,
            "resolution": resolution
        })

    def add_round_summary(self, summary: str):
        """Add a round summary."""
        self.round_summaries.append(summary)

    def get_context_for_prompt(self, shared_kb: SharedKnowledgeBase) -> str:
        """Generate context string for prompt injection."""
        identity = f"""
=== YOUR IDENTITY ===
You are Annotator {self.annotator_id}. Apply consistent, objective analysis.
"""

        rules = f"""
=== LEARNED RULES (from team deliberations) ===
{shared_kb.get_rules_for_prompt()}
"""

        anchors = f"""
=== ANCHOR EXAMPLES (team agreed on these) ===
{shared_kb.get_anchors_for_prompt()}
"""

        corrections = ""
        if self.correction_log:
            recent = self.correction_log[-5:]
            corrections = "\n=== MY CORRECTION HISTORY (learn from these) ===\n"
            for c in recent:
                corrections += f"- Trace {c['trace_id']}: I said {c['my_answer']}, "
                corrections += f"correct was {c['correct_answer']}. Reason: {c['reason']}\n"

        recent_ann = ""
        if self.annotation_history:
            recent = self.annotation_history[-5:]
            recent_ann = "\n=== MY RECENT ANNOTATIONS ===\n"
            for a in recent:
                recent_ann += f"- Trace {a['trace_id']} ({a['phase']})\n"

        return identity + rules + anchors + corrections + recent_ann


# =============================================================================
# PHASE 1: INDEPENDENT ERROR DISCOVERY
# =============================================================================

class ErrorDiscovery:
    """
    Phase 1: Each annotator independently identifies potential failures.
    Output: Raw list of proposed errors from each annotator.
    """

    def __init__(self, client: TextProvider, annotator_memories: List[AnnotatorMemory],
                 shared_kb: SharedKnowledgeBase):
        self.client = client
        self.annotator_memories = annotator_memories
        self.shared_kb = shared_kb

    def discover_errors(self, parsed_trace: Dict, taxonomy: Dict) -> Dict:
        """
        Have all annotators independently identify potential errors.
        Returns raw proposals without filtering.
        """
        trace_summary = self._format_trace(parsed_trace)
        taxonomy_summary = self._format_taxonomy_summary(taxonomy)

        proposals = {}
        for memory in self.annotator_memories:
            try:
                result = self._annotator_discover(memory, trace_summary, taxonomy_summary)
                proposals[memory.annotator_id] = result.get('errors', [])

                memory.add_annotation(
                    parsed_trace.get('problem_id', 'unknown'),
                    "error_discovery",
                    result
                )
            except Exception as e:
                progress(f"  [!] {memory.annotator_id} discovery error: {e}")
                proposals[memory.annotator_id] = []

        return {
            "proposals": proposals,
            "trace_id": parsed_trace.get('problem_id', 'unknown')
        }

    def _annotator_discover(self, memory: AnnotatorMemory, trace_summary: str,
                            taxonomy_summary: str) -> Dict:
        """Single annotator discovers potential errors."""
        context = memory.get_context_for_prompt(self.shared_kb)

        prompt = f"""{context}

=== TASK: ERROR DISCOVERY ===
Analyze this trace and identify ALL potential failures you observe.
Do NOT assign codes yet - just describe what went wrong.

FAILURE TYPES TO LOOK FOR:
- Type A: Infrastructure failures (empty output, timeout, truncation, connection issues)
- Type B: Role failures (agent didn't fulfill its role - wrong solution, bad verification, etc.)
- Type C: Reasoning failures (logic errors, calculation mistakes, incorrect theorems)

NOTE: A single agent can have MULTIPLE failures of different types.
For example, SOLVER could have both a truncation issue (A) AND a calculation error (C).

TRACE:
{trace_summary}

TAXONOMY REFERENCE (for context on what kinds of errors exist):
{taxonomy_summary}

For EACH failure you find, describe:
1. A unique ID (F1, F2, F3, etc.)
2. Which agent is involved (solver, checker, refiner, arbiter, or "system" for infrastructure)
3. What went wrong (observable symptom, not the underlying cause yet)
4. Where in the trace this appears
5. Your confidence (high/medium/low)

OUTPUT JSON:
{{
    "errors": [
        {{
            "failure_id": "F1",
            "agent": "solver/checker/refiner/arbiter/system",
            "what_went_wrong": "Brief description of the observable failure",
            "location": "Where in trace",
            "confidence": "high/medium/low"
        }}
    ],
    "no_errors_found": false,
    "reasoning": "Brief explanation of your analysis"
}}

If you find NO errors, set "no_errors_found": true and "errors": []."""

        response = call_llm(self.client, prompt)
        result = extract_json(response)

        if not isinstance(result, dict):
            result = {"errors": [], "no_errors_found": True}

        return result

    def _format_trace(self, parsed_trace: Dict) -> str:
        """Format parsed trace for error discovery."""
        lines = [f"Problem ID: {parsed_trace.get('problem_id', 'unknown')}"]
        lines.append(f"Final Verdict: {parsed_trace.get('final_verdict', 'UNKNOWN')}")
        lines.append(f"Final Answer: {parsed_trace.get('final_answer', 'N/A')}")

        infra = parsed_trace.get('infrastructure_issues', [])
        if infra:
            lines.append("\nINFRASTRUCTURE ISSUES DETECTED:")
            for issue in infra:
                lines.append(f"  - {issue.get('type')}: {issue.get('description')}")

        lines.append("\nAGENT OUTPUTS:")
        for output in parsed_trace.get('agent_outputs', []):
            agent = output.get('agent', '?')
            role = output.get('role', '?')
            content = output.get('content', '')[:500]
            status = output.get('status', '?')
            lines.append(f"\n[{agent.upper()} ({role})] Status: {status}")
            lines.append(content)

        raw = parsed_trace.get('raw_trajectory', '')
        if raw and len(raw) > 100:
            lines.append("\nRAW TRAJECTORY (excerpt):")
            lines.append(raw[:1000])

        return "\n".join(lines)

    def _format_taxonomy_summary(self, taxonomy: Dict) -> str:
        """Format taxonomy for prompt."""
        lines = []
        for cat in ['category_a', 'category_b', 'category_c']:
            cat_name = cat.split('_')[1].upper()
            lines.append(f"\nCATEGORY {cat_name}:")
            codes = taxonomy.get(cat, {})
            if isinstance(codes, dict):
                for code_id, code in list(codes.items())[:8]:
                    name = code.get('name', '?') if isinstance(code, dict) else '?'
                    lines.append(f"  {code_id}: {name}")
        return "\n".join(lines)


# =============================================================================
# PHASE 2: ERROR RECONCILIATION
# =============================================================================

class ErrorReconciliation:
    """
    Phase 2: Show all annotators what everyone found.
    Have them discuss and vote to create a unified error list.
    """

    def __init__(self, client: TextProvider, annotator_memories: List[AnnotatorMemory],
                 shared_kb: SharedKnowledgeBase):
        self.client = client
        self.annotator_memories = annotator_memories
        self.shared_kb = shared_kb

    def reconcile(self, discovery_result: Dict, parsed_trace: Dict) -> Dict:
        """
        Reconcile error proposals from all annotators.
        Show each annotator what others found, let them vote.
        """
        proposals = discovery_result.get('proposals', {})
        trace_id = discovery_result.get('trace_id', 'unknown')

        # Step 1: Aggregate and deduplicate proposals
        all_proposed_errors = self._aggregate_proposals(proposals)

        if not all_proposed_errors:
            return {
                "agreed_errors": [],
                "rejected_errors": [],
                "trace_id": trace_id
            }

        # Step 2: Show all annotators the full list and have them vote
        votes = {}
        for memory in self.annotator_memories:
            try:
                vote_result = self._annotator_vote(
                    memory, all_proposed_errors, proposals, parsed_trace
                )
                votes[memory.annotator_id] = vote_result
            except Exception as e:
                progress(f"  [!] {memory.annotator_id} vote error: {e}")
                votes[memory.annotator_id] = {"accepted": [], "rejected": []}

        # Step 3: Tally votes and determine agreed errors
        agreed_errors, rejected_errors = self._tally_votes(all_proposed_errors, votes)

        # Step 4: For borderline cases, run a discussion round
        if self._has_borderline_cases(all_proposed_errors, votes):
            agreed_errors, rejected_errors = self._discuss_borderline(
                all_proposed_errors, votes, agreed_errors, rejected_errors, parsed_trace
            )

        return {
            "agreed_errors": agreed_errors,
            "rejected_errors": rejected_errors,
            "all_proposals": all_proposed_errors,
            "votes": votes,
            "trace_id": trace_id
        }

    def _aggregate_proposals(self, proposals: Dict) -> List[Dict]:
        """
        Aggregate proposals from all annotators, merging similar ones.
        Uses fuzzy matching to catch errors that describe the same issue.
        """
        all_errors = []
        seen_keys = {}

        for annotator_id, errors in proposals.items():
            if not isinstance(errors, list):
                continue

            for error in errors:
                if not isinstance(error, dict):
                    continue

                # Create a key for deduplication
                key = self._make_error_key(error)

                # Check for exact match first
                if key in seen_keys:
                    seen_keys[key]['proposers'].add(annotator_id)
                else:
                    # Check for fuzzy match with existing keys
                    merged = False
                    for existing_key in list(seen_keys.keys()):
                        if self._are_similar_keys(key, existing_key):
                            seen_keys[existing_key]['proposers'].add(annotator_id)
                            # Keep longer description
                            existing_desc = seen_keys[existing_key]['description']
                            new_desc = error.get('what_went_wrong', error.get('description', ''))
                            if len(new_desc) > len(existing_desc):
                                seen_keys[existing_key]['description'] = new_desc
                            merged = True
                            break

                    if not merged:
                        # New error - with defensive handling for all fields
                        agent = error.get('agent', 'unknown')
                        if isinstance(agent, list):
                            agent = agent[0] if agent else 'unknown'
                        agent = str(agent) if agent else 'unknown'

                        desc = error.get('what_went_wrong', error.get('description', ''))
                        if isinstance(desc, list):
                            desc = ' '.join(str(d) for d in desc)
                        desc = str(desc) if desc else ''

                        location = error.get('location', '')
                        if isinstance(location, list):
                            location = ' '.join(str(part) for part in location)
                        location = str(location) if location else ''

                        confidence = error.get('confidence', 'medium')
                        if isinstance(confidence, list):
                            confidence = confidence[0] if confidence else 'medium'
                        confidence = str(confidence) if confidence else 'medium'

                        original_id = error.get('failure_id', '')
                        if isinstance(original_id, list):
                            original_id = original_id[0] if original_id else ''
                        original_id = str(original_id) if original_id else ''

                        error_entry = {
                            "error_id": f"E{len(all_errors) + 1}",
                            "agent": agent,
                            "description": desc,
                            "location": location,
                            "confidence": confidence,
                            "proposers": {annotator_id},
                            "original_id": original_id
                        }
                        seen_keys[key] = error_entry
                        all_errors.append(error_entry)

        # Convert proposers sets to lists for JSON serialization
        for error in all_errors:
            error['proposers'] = list(error['proposers'])
            error['proposer_count'] = len(error['proposers'])

        return all_errors

    def _are_similar_keys(self, key1: str, key2: str) -> bool:
        """Check if two error keys are similar enough to merge."""
        parts1 = key1.split('|')
        parts2 = key2.split('|')

        if len(parts1) != 2 or len(parts2) != 2:
            return False

        agent1, words1 = parts1
        agent2, words2 = parts2

        # Agents must be same or one is 'unknown'
        if agent1 != agent2 and agent1 != 'unknown' and agent2 != 'unknown':
            # Allow solver vs SOLVER (case diff already handled, but check similar)
            if agent1.lower() != agent2.lower():
                return False

        # Compare word overlap (Jaccard similarity)
        set1 = set(words1.split('-')) if words1 else set()
        set2 = set(words2.split('-')) if words2 else set()

        if not set1 or not set2:
            return False

        intersection = len(set1 & set2)
        union = len(set1 | set2)

        similarity = intersection / union if union > 0 else 0

        # Require at least 50% overlap for merge
        return similarity >= 0.5

    def _make_error_key(self, error: Dict) -> str:
        """Create a key for error deduplication with improved fuzzy matching."""
        agent = error.get('agent', '') or ''
        # Handle case where agent is a list
        if isinstance(agent, list):
            agent = agent[0] if agent else ''
        agent = str(agent).lower().strip()

        desc = error.get('what_went_wrong', error.get('description', ''))
        # Handle case where description is a list
        if isinstance(desc, list):
            desc = ' '.join(str(d) for d in desc)
        desc = str(desc).lower()

        # Normalize agent names
        agent_map = {
            'solver': 'solver', 'solve': 'solver',
            'checker': 'checker', 'check': 'checker', 'verify': 'checker',
            'refiner': 'refiner', 'refine': 'refiner', 'revise': 'refiner',
            'arbiter': 'arbiter', 'arbitrate': 'arbiter',
            'system': 'system', 'infrastructure': 'system'
        }
        agent = agent_map.get(agent, agent)

        # Extract key words, removing stop words
        desc = re.sub(r'[^a-z0-9\s]', ' ', desc)
        stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'this', 'that',
                      'which', 'with', 'from', 'for', 'and', 'but', 'not', 'has',
                      'have', 'had', 'been', 'being', 'indicates', 'indicating',
                      'observed', 'shows', 'showing', 'produced', 'outputs'}

        # Key error terms to prioritize
        key_terms = {'truncat', 'timeout', 'empty', 'incorrect', 'wrong', 'invalid',
                     'fail', 'error', 'crash', 'incomplete', 'mismatch', 'formula',
                     'calculation', 'derivation', 'result', 'solution', 'output',
                     'verdict', 'reject', 'accept', 'parity', 'iteration'}

        words = desc.split()
        priority_words = []
        other_words = []

        for w in words:
            if len(w) <= 3 or w in stop_words:
                continue
            if any(term in w for term in key_terms):
                priority_words.append(w)
            else:
                other_words.append(w)

        # Build key from priority words first, then others
        selected = priority_words[:4] + other_words[:3]
        selected = sorted(set(selected))[:5]

        return f"{agent}|{'-'.join(selected)}"

    def _annotator_vote(self, memory: AnnotatorMemory, all_errors: List[Dict],
                        original_proposals: Dict, parsed_trace: Dict) -> Dict:
        """Have an annotator vote on all proposed errors."""
        context = memory.get_context_for_prompt(self.shared_kb)

        # Format all proposed errors
        errors_text = []
        for error in all_errors:
            proposers = ', '.join(error['proposers'])
            errors_text.append(
                f"{error['error_id']}: {error['description']}\n"
                f"   Agent: {error['agent']}, Location: {error['location']}\n"
                f"   Proposed by: {proposers} ({error['proposer_count']}/{len(ANNOTATOR_IDS)} annotators)"
            )

        # Show what this annotator originally found
        my_proposals = original_proposals.get(memory.annotator_id, [])
        my_proposals_text = "\n".join([
            f"- {p.get('what_went_wrong', p.get('description', ''))}"
            for p in my_proposals
        ]) if my_proposals else "None"

        prompt = f"""{context}

=== TASK: ERROR RECONCILIATION ===
Your team has analyzed this trace and proposed the following errors.
Review ALL proposals and vote on which ones are REAL errors.

YOUR ORIGINAL PROPOSALS:
{my_proposals_text}

ALL PROPOSED ERRORS (from all annotators):
{chr(10).join(errors_text)}

TRACE SUMMARY (for reference):
Problem ID: {parsed_trace.get('problem_id', 'unknown')}
Final Verdict: {parsed_trace.get('final_verdict', 'UNKNOWN')}

TASK:
1. Review each proposed error
2. Vote ACCEPT if you agree it's a real error
3. Vote REJECT if you think it's NOT a real error (with brief reason)
4. You can accept errors you didn't originally propose if they're valid
5. You can reject errors you DID propose if, upon reflection, they're not valid

OUTPUT JSON:
{{
    "accepted": ["{all_errors[0]['error_id'] if all_errors else 'E1'}", ...],
    "rejected": [
        {{"error_id": "EX", "reason": "Why this is not a real error"}}
    ],
    "reasoning": "Brief explanation of your decisions"
}}"""

        response = call_llm(self.client, prompt)
        result = extract_json(response)

        if not isinstance(result, dict):
            # Default: accept all errors this annotator originally proposed
            my_error_ids = []
            for error in all_errors:
                if memory.annotator_id in error['proposers']:
                    my_error_ids.append(error['error_id'])
            return {"accepted": my_error_ids, "rejected": []}

        return result

    def _tally_votes(self, all_errors: List[Dict], votes: Dict) -> Tuple[List[Dict], List[Dict]]:
        """Tally votes and determine which errors are agreed upon."""
        agreed = []
        rejected = []

        for error in all_errors:
            error_id = error['error_id']
            accept_count = 0

            for annotator_id, vote in votes.items():
                accepted = vote.get('accepted', [])
                if error_id in accepted:
                    accept_count += 1

            error['accept_votes'] = accept_count
            error['vote_count'] = len(votes)

            if accept_count >= Config.ERROR_CONFIRMATION_THRESHOLD:
                agreed.append(error)
            else:
                rejected.append(error)

        return agreed, rejected

    def _has_borderline_cases(self, all_errors: List[Dict], votes: Dict) -> bool:
        """Check if there are borderline cases (close to threshold)."""
        for error in all_errors:
            accept_count = error.get('accept_votes', 0)
            # Borderline: 2 votes (1 away from threshold of 3)
            if accept_count == Config.ERROR_CONFIRMATION_THRESHOLD - 1:
                return True
        return False

    def _discuss_borderline(self, all_errors: List[Dict], votes: Dict,
                           agreed: List[Dict], rejected: List[Dict],
                           parsed_trace: Dict) -> Tuple[List[Dict], List[Dict]]:
        """
        Run a discussion round for borderline cases.
        Show annotators the voting results and let them reconsider.
        """
        # Find borderline errors
        borderline = [e for e in all_errors
                     if e.get('accept_votes', 0) == Config.ERROR_CONFIRMATION_THRESHOLD - 1]

        if not borderline:
            return agreed, rejected

        progress(f"    Discussing {len(borderline)} borderline error(s)")

        # Format discussion prompt
        borderline_text = []
        for error in borderline:
            accepters = [ann for ann, v in votes.items() if error['error_id'] in v.get('accepted', [])]
            rejecters = [ann for ann, v in votes.items() if error['error_id'] not in v.get('accepted', [])]

            reject_reasons = []
            for ann, v in votes.items():
                for rej in v.get('rejected', []):
                    if isinstance(rej, dict) and rej.get('error_id') == error['error_id']:
                        reject_reasons.append(f"{ann}: {rej.get('reason', 'No reason given')}")

            borderline_text.append(
                f"{error['error_id']}: {error['description']}\n"
                f"   Accepted by: {', '.join(accepters)}\n"
                f"   Not accepted by: {', '.join(rejecters)}\n"
                f"   Rejection reasons: {'; '.join(reject_reasons) if reject_reasons else 'None given'}"
            )

        # Have each annotator reconsider
        new_votes = {}
        for memory in self.annotator_memories:
            prompt = f"""=== DISCUSSION: BORDERLINE ERRORS ===

The following errors received split votes. Please reconsider after seeing others' perspectives.

BORDERLINE ERRORS:
{chr(10).join(borderline_text)}

After seeing the discussion, do you want to change any votes?

OUTPUT JSON:
{{
    "changes": [
        {{"error_id": "EX", "new_vote": "accept/reject", "reason": "Why I changed my mind"}}
    ],
    "no_changes": true/false
}}"""

            response = call_llm(self.client, prompt)
            result = extract_json(response)

            if isinstance(result, dict) and result.get('changes'):
                new_votes[memory.annotator_id] = result['changes']

        # Apply vote changes
        for error in borderline:
            error_id = error['error_id']
            current_accepts = error.get('accept_votes', 0)

            for annotator_id, changes in new_votes.items():
                for change in changes:
                    if change.get('error_id') == error_id:
                        old_accepted = error_id in votes[annotator_id].get('accepted', [])
                        new_vote = change.get('new_vote', '').lower()

                        if new_vote == 'accept' and not old_accepted:
                            current_accepts += 1
                        elif new_vote == 'reject' and old_accepted:
                            current_accepts -= 1

            error['accept_votes'] = current_accepts

            # Recheck threshold
            if current_accepts >= Config.ERROR_CONFIRMATION_THRESHOLD:
                if error not in agreed:
                    agreed.append(error)
                    if error in rejected:
                        rejected.remove(error)

        return agreed, rejected


# =============================================================================
# PHASE 3: HIGH-LEVEL FAILURE TYPING
# =============================================================================

class FailureTyping:
    """
    Phase 3: Categorize agreed errors into A/B/C types.
    This happens AFTER agreeing on what the failures are, but BEFORE coding.
    """

    def __init__(self, client: TextProvider, annotator_memories: List[AnnotatorMemory],
                 shared_kb: SharedKnowledgeBase):
        self.client = client
        self.annotator_memories = annotator_memories
        self.shared_kb = shared_kb

    def type_failures(self, agreed_errors: List[Dict], parsed_trace: Dict,
                      taxonomy: Dict) -> Dict:
        """
        Have annotators assign high-level types (A/B/C) to agreed errors.
        """
        if not agreed_errors:
            return {"typed_errors": [], "typing_agreement": {}}

        # Each annotator types the errors
        type_assignments = {}
        for memory in self.annotator_memories:
            try:
                result = self._annotator_type(memory, agreed_errors, parsed_trace, taxonomy)
                type_assignments[memory.annotator_id] = result
            except Exception as e:
                progress(f"  [!] {memory.annotator_id} typing error: {e}")
                type_assignments[memory.annotator_id] = {}

        # Consolidate types via majority vote
        typed_errors = []
        typing_agreement = {}

        for error in agreed_errors:
            error_id = error.get('error_id', f"E{len(typed_errors)+1}")
            # Ensure error_id is a string
            if isinstance(error_id, list):
                error_id = error_id[0] if error_id else f"E{len(typed_errors)+1}"
            error_id = str(error_id)

            type_votes = Counter()

            for ann_id, assignments in type_assignments.items():
                assigned_data = assignments.get(error_id, {})
                if isinstance(assigned_data, dict):
                    assigned_type = assigned_data.get('type', 'C')
                elif isinstance(assigned_data, str):
                    assigned_type = assigned_data
                else:
                    assigned_type = 'C'

                # Ensure assigned_type is a string (not a list)
                if isinstance(assigned_type, list):
                    assigned_type = assigned_type[0] if assigned_type else 'C'
                if not isinstance(assigned_type, str):
                    assigned_type = str(assigned_type) if assigned_type else 'C'

                # Normalize to single letter
                assigned_type = assigned_type.upper().strip()
                if assigned_type not in ('A', 'B', 'C'):
                    assigned_type = 'C'  # Default to C

                type_votes[assigned_type] += 1

            # Majority type
            majority_type = type_votes.most_common(1)[0][0] if type_votes else 'C'
            agreement_level = type_votes[majority_type] / len(type_assignments) if type_assignments else 0

            typed_error = error.copy()
            typed_error['type'] = majority_type
            typed_error['type_agreement'] = agreement_level
            typed_errors.append(typed_error)

            typing_agreement[error_id] = {
                "votes": dict(type_votes),
                "final_type": majority_type,
                "agreement": agreement_level
            }

        return {
            "typed_errors": typed_errors,
            "typing_agreement": typing_agreement,
            "individual_assignments": type_assignments
        }

    def _annotator_type(self, memory: AnnotatorMemory, agreed_errors: List[Dict],
                        parsed_trace: Dict, taxonomy: Dict) -> Dict:
        """Single annotator assigns types to errors."""
        context = memory.get_context_for_prompt(self.shared_kb)

        errors_text = "\n".join([
            f"{e['error_id']}: {e['description']} (Agent: {e.get('agent', 'unknown')})"
            for e in agreed_errors
        ])

        prompt = f"""{context}

=== TASK: FAILURE TYPE CLASSIFICATION ===
The team agreed these errors exist. Now classify each into Type A, B, or C.

TYPE DEFINITIONS:
- Type A: HOW infrastructure failed
  * Empty output, timeout, truncation, connection errors
  * The system didn't produce a response (not about content quality)

- Type B: HOW the agent's role failed (observable consequence)
  * Solver: produced wrong solution
  * Checker: accepted invalid / rejected valid
  * Refiner: failed to fix identified issues
  * Arbiter: made wrong final decision

- Type C: WHY reasoning failed (underlying logic flaw)
  * Calculation errors, proof mistakes
  * Misapplied theorems, wrong assumptions
  * Missing case analysis, parity errors

NOTE: One agent can have MULTIPLE failure types simultaneously.
E.g., SOLVER could have Type A (truncated) AND Type C (calculation error).

AGREED ERRORS:
{errors_text}

TRACE CONTEXT:
Problem ID: {parsed_trace.get('problem_id', 'unknown')}
Final Verdict: {parsed_trace.get('final_verdict', 'UNKNOWN')}

OUTPUT JSON:
{{
    "{agreed_errors[0]['error_id'] if agreed_errors else 'E1'}": {{"type": "A/B/C", "reason": "Why this type"}},
    ...
}}"""

        response = call_llm(self.client, prompt)
        result = extract_json(response)

        if not isinstance(result, dict):
            return {}

        return result


# =============================================================================
# PHASE 4: CODE ASSIGNMENT WITH VALIDATION
# =============================================================================

class CodeAssignment:
    """
    Phase 4: Assign specific taxonomy codes to typed errors.
    Includes validation to reject invalid codes.
    """

    def __init__(self, client: TextProvider, annotator_memories: List[AnnotatorMemory],
                 shared_kb: SharedKnowledgeBase):
        self.client = client
        self.annotator_memories = annotator_memories
        self.shared_kb = shared_kb

    def assign_codes(self, typed_errors: List[Dict], parsed_trace: Dict,
                     taxonomy: Dict) -> Dict:
        """
        Have all annotators assign codes to typed errors.
        Validate that codes are from the taxonomy.
        """
        if not typed_errors:
            return {"assignments": {}, "consolidated": {}, "agreement_matrix": {}}

        # Get valid codes from taxonomy
        valid_codes = self._get_valid_codes(taxonomy)

        # Each annotator assigns codes
        assignments = {}
        for memory in self.annotator_memories:
            try:
                result = self._annotator_assign(
                    memory, typed_errors, parsed_trace, taxonomy, valid_codes
                )

                # Validate and filter invalid codes
                validated = self._validate_assignments(result, valid_codes, typed_errors)
                assignments[memory.annotator_id] = validated

                memory.add_annotation(
                    parsed_trace.get('problem_id', 'unknown'),
                    "code_assignment",
                    {"codes": validated}
                )
            except Exception as e:
                progress(f"  [!] {memory.annotator_id} assignment error: {e}")
                assignments[memory.annotator_id] = {}

        # Build agreement matrix
        agreement_matrix = self._build_agreement_matrix(assignments, typed_errors)

        # Consolidated (majority vote per error)
        consolidated = self._consolidate_assignments(assignments, typed_errors)

        return {
            "assignments": assignments,
            "consolidated": consolidated,
            "agreement_matrix": agreement_matrix
        }

    def _get_valid_codes(self, taxonomy: Dict) -> Set[str]:
        """Extract all valid codes from taxonomy."""
        valid = set()
        for cat in ['category_a', 'category_b', 'category_c']:
            codes = taxonomy.get(cat, {})
            if isinstance(codes, dict):
                valid.update(codes.keys())
        return valid

    def _annotator_assign(self, memory: AnnotatorMemory, typed_errors: List[Dict],
                          parsed_trace: Dict, taxonomy: Dict, valid_codes: Set[str]) -> Dict:
        """Single annotator assigns codes to errors."""
        context = memory.get_context_for_prompt(self.shared_kb)

        # Format typed errors
        errors_text = "\n".join([
            f"{e['error_id']} [Type {e['type']}]: {e['description']} (Agent: {e.get('agent', 'unknown')})"
            for e in typed_errors
        ])

        # Format taxonomy by category
        taxonomy_text = self._format_taxonomy(taxonomy)

        # List valid codes explicitly
        valid_codes_list = sorted(valid_codes)

        # Get confusion guidance if available
        confusion_guidance = self.shared_kb.get_confusion_guidance_for_prompt()

        prompt = f"""{context}

=== TASK: CODE ASSIGNMENT ===
Assign specific taxonomy codes to each error.

CONSTRAINTS:
- Type A errors → ONLY codes starting with A. ({', '.join(c for c in valid_codes_list if c.startswith('A.'))})
- Type B errors → ONLY codes starting with B. ({', '.join(c for c in valid_codes_list if c.startswith('B.'))})
- Type C errors → ONLY codes starting with C. ({', '.join(c for c in valid_codes_list if c.startswith('C.'))})

IMPORTANT: Use ONLY codes from the list above. Do NOT invent codes.
{confusion_guidance}

TYPED ERRORS:
{errors_text}

TAXONOMY:
{taxonomy_text}

TRACE (for reference):
{parsed_trace.get('raw_trajectory', '')[:1500]}

OUTPUT JSON:
{{
    "{typed_errors[0]['error_id'] if typed_errors else 'E1'}": {{
        "codes": ["X.N"],
        "reasoning": "Why this code"
    }},
    ...
}}"""

        response = call_llm(self.client, prompt)
        result = extract_json(response)

        if not isinstance(result, dict):
            return {}

        # Extract just the codes - with robust flattening
        assignments = {}
        for error_id, data in result.items():
            codes = []
            if isinstance(data, dict):
                raw_codes = data.get('codes', [])
            elif isinstance(data, list):
                raw_codes = data
            elif isinstance(data, str):
                raw_codes = [data]
            else:
                raw_codes = []

            # Flatten nested lists and ensure all codes are strings
            codes = self._flatten_codes(raw_codes)
            if codes:
                assignments[error_id] = codes

        return assignments

    def _flatten_codes(self, codes: Any) -> List[str]:
        """Flatten potentially nested list of codes into flat list of strings."""
        result = []
        if isinstance(codes, str):
            result.append(codes)
        elif isinstance(codes, list):
            for item in codes:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(item, list):
                    # Recursively flatten
                    result.extend(self._flatten_codes(item))
                elif isinstance(item, dict):
                    # Handle {"code": "X.N"} format
                    if 'code' in item:
                        result.append(str(item['code']))
        return result

    def _validate_assignments(self, assignments: Dict, valid_codes: Set[str],
                              typed_errors: List[Dict]) -> Dict:
        """
        Validate assignments and filter out invalid codes.
        """
        validated = {}

        # Build error_types dict with defensive handling
        error_types = {}
        for e in typed_errors:
            eid = e.get('error_id', '')
            etype = e.get('type', 'C')
            # Ensure error_id is a string
            if not isinstance(eid, str):
                eid = str(eid) if eid else f"E{len(error_types)+1}"
            # Ensure type is a string
            if not isinstance(etype, str):
                etype = etype[0] if isinstance(etype, list) and etype else 'C'
            error_types[eid] = etype

        for error_id, codes in assignments.items():
            # Ensure error_id is a string
            if not isinstance(error_id, str):
                error_id = str(error_id) if error_id else "unknown"

            # Flatten codes
            codes = self._flatten_codes(codes)

            error_type = error_types.get(error_id, 'C')
            valid_prefix = f"{error_type}."

            valid_assigned = []
            for code in codes:
                if not isinstance(code, str):
                    continue

                # Check if code is in taxonomy
                if code in valid_codes:
                    # Check if code matches error type
                    if code.startswith(valid_prefix):
                        valid_assigned.append(code)

            validated[error_id] = valid_assigned

        return validated

    def _format_taxonomy(self, taxonomy: Dict) -> str:
        """Format full taxonomy for code assignment."""
        lines = []
        for cat in ['category_a', 'category_b', 'category_c']:
            cat_name = cat.split('_')[1].upper()
            lines.append(f"\n=== CATEGORY {cat_name} ===")
            codes = taxonomy.get(cat, {})
            if isinstance(codes, dict):
                for code_id, code in codes.items():
                    if isinstance(code, dict):
                        name = code.get('name', '?')
                        defn = code.get('definition', '')[:100]
                        lines.append(f"{code_id}: {name}")
                        lines.append(f"   {defn}")
        return "\n".join(lines)

    def _build_agreement_matrix(self, assignments: Dict,
                                typed_errors: List[Dict]) -> Dict:
        """Build matrix showing agreement between annotators."""
        matrix = {}
        annotator_ids = list(assignments.keys())

        for error in typed_errors:
            error_id = error.get('error_id', '')
            matrix[error_id] = {
                "codes_by_annotator": {},
                "agreement_level": 0.0
            }

            all_codes = []
            for ann_id in annotator_ids:
                codes = assignments.get(ann_id, {}).get(error_id, [])
                # Flatten and ensure strings only
                codes = self._flatten_codes(codes)
                matrix[error_id]["codes_by_annotator"][ann_id] = codes
                all_codes.extend(codes)

            if all_codes:
                # Filter to ensure only strings for Counter
                all_codes = [c for c in all_codes if isinstance(c, str)]
                if all_codes:
                    code_counts = Counter(all_codes)
                    unanimous = [c for c, count in code_counts.items()
                                if count == len(annotator_ids)]
                    matrix[error_id]["agreement_level"] = len(unanimous) / len(set(all_codes))

        return matrix

    def _consolidate_assignments(self, assignments: Dict,
                                 typed_errors: List[Dict]) -> Dict:
        """Consolidate assignments via majority vote."""
        consolidated = {}
        annotator_ids = list(assignments.keys())

        for error in typed_errors:
            error_id = error.get('error_id', '')

            all_codes = []
            for ann_id in annotator_ids:
                codes = assignments.get(ann_id, {}).get(error_id, [])
                # Flatten and ensure strings only
                flattened = self._flatten_codes(codes)
                all_codes.extend(flattened)

            if all_codes:
                # Filter out any non-string items that slipped through
                all_codes = [c for c in all_codes if isinstance(c, str)]
                code_counts = Counter(all_codes)
                threshold = len(annotator_ids) / 2
                consolidated[error_id] = [
                    code for code, count in code_counts.items()
                    if count >= threshold
                ]
            else:
                consolidated[error_id] = []

        return consolidated


# =============================================================================
# PHASE 5: CODE DELIBERATION
# =============================================================================

class CodeDeliberation:
    """
    Phase 5: Multi-turn discussion for code disagreements.
    Shows annotators what others assigned and why.
    """

    def __init__(self, client: TextProvider, annotator_memories: List[AnnotatorMemory],
                 shared_kb: SharedKnowledgeBase):
        self.client = client
        self.annotator_memories = annotator_memories
        self.shared_kb = shared_kb

    def deliberate(self, typed_errors: List[Dict], assignments: Dict,
                   agreement_matrix: Dict, taxonomy: Dict) -> Dict:
        """
        Run deliberation for errors with low agreement.
        """
        # Find errors with low agreement
        low_agreement_errors = []
        for error in typed_errors:
            error_id = error['error_id']
            matrix_data = agreement_matrix.get(error_id, {})
            if matrix_data.get('agreement_level', 0) < 0.5:
                low_agreement_errors.append(error)

        if not low_agreement_errors:
            return {
                "deliberation_needed": False,
                "final_assignments": {},
                "learned_rules": []
            }

        progress(f"    Deliberating on {len(low_agreement_errors)} error(s) with low agreement")

        final_assignments = {}
        learned_rules = []

        for error in low_agreement_errors:
            error_id = error['error_id']

            # Run multi-turn deliberation
            result = self._deliberate_single_error(
                error, assignments, agreement_matrix, taxonomy
            )

            final_assignments[error_id] = result.get('final_codes', [])

            if result.get('learned_rule'):
                learned_rules.append(result['learned_rule'])
                self.shared_kb.add_rule(result['learned_rule'])

            # Update annotator memories with corrections
            for correction in result.get('corrections', []):
                ann_id = correction.get('annotator')
                if correction.get('was_wrong'):
                    for memory in self.annotator_memories:
                        if memory.annotator_id == ann_id:
                            memory.add_correction(
                                error_id,
                                correction.get('their_codes'),
                                result.get('final_codes'),
                                correction.get('reason', '')
                            )

        return {
            "deliberation_needed": True,
            "final_assignments": final_assignments,
            "learned_rules": learned_rules,
            "errors_deliberated": [e['error_id'] for e in low_agreement_errors]
        }

    def _deliberate_single_error(self, error: Dict, assignments: Dict,
                                  agreement_matrix: Dict, taxonomy: Dict) -> Dict:
        """
        Run deliberation for a single error.
        Enhanced to detect overlapping codes and create better rules.
        """
        error_id = error['error_id']
        error_type = error.get('type', 'C')

        # Collect all codes assigned by different annotators
        all_assigned_codes = set()
        for ann_id, ann_assignments in assignments.items():
            codes = ann_assignments.get(error_id, [])
            if isinstance(codes, list):
                for c in codes:
                    if isinstance(c, str):
                        all_assigned_codes.add(c)

        # Record confusion pairs for codes that disagree
        codes_list = list(all_assigned_codes)
        for i, c1 in enumerate(codes_list):
            for c2 in codes_list[i+1:]:
                self.shared_kb.record_confusion(c1, c2)

        # Check if disagreeing codes are known to overlap
        overlapping_involved = []
        disambiguation_guidance = ""
        for i, c1 in enumerate(codes_list):
            for c2 in codes_list[i+1:]:
                if self.shared_kb.are_codes_overlapping(c1, c2):
                    overlapping_involved.append((c1, c2))
                    guidance = self.shared_kb.get_disambiguation_guidance(c1, c2)
                    if guidance:
                        disambiguation_guidance += f"\n{c1} vs {c2}: {guidance}"

        # Format what each annotator assigned
        assignment_text = []
        for ann_id, ann_assignments in assignments.items():
            codes = ann_assignments.get(error_id, [])
            assignment_text.append(f"{ann_id}: {codes if codes else '(no codes)'}")

        # Get relevant taxonomy codes
        cat_key = f"category_{error_type.lower()}"
        relevant_codes = taxonomy.get(cat_key, {})
        codes_text = "\n".join([
            f"{code_id}: {code.get('name', '?')} - {code.get('definition', '')[:80]}"
            for code_id, code in relevant_codes.items()
            if isinstance(code, dict)
        ])

        # Build overlap warning if applicable
        overlap_warning = ""
        if overlapping_involved:
            overlap_warning = f"""
=== WARNING: OVERLAPPING CODES DETECTED ===
The following code pairs are KNOWN to be semantically similar:
{', '.join([f'{c1} vs {c2}' for c1, c2 in overlapping_involved])}

When codes overlap, pick the one with STRONGEST OBSERVABLE EVIDENCE.
If no clear evidence distinguishes them, use the more GENERAL code.
{disambiguation_guidance}
"""

        prompt = f"""=== CODE DELIBERATION ===

ERROR BEING CODED:
ID: {error_id}
Type: {error_type}
Agent: {error.get('agent', 'N/A')}
Description: {error.get('description', '')}

CURRENT ASSIGNMENTS (showing disagreement):
{chr(10).join(assignment_text)}
{overlap_warning}
RELEVANT TAXONOMY CODES (Type {error_type}):
{codes_text}

TASK:
1. Analyze the disagreement
2. Determine the CORRECT code(s) for this error
3. Identify which annotators were wrong and why
4. Extract a rule to prevent this disagreement in the future

OUTPUT JSON:
{{
    "final_codes": ["X.N"],
    "reasoning": "Why these codes are correct based on the taxonomy definitions",
    "corrections": [
        {{
            "annotator": "Alpha",
            "was_wrong": true,
            "their_codes": ["..."],
            "reason": "What they got wrong"
        }}
    ],
    "learned_rule": {{
        "situation": "When you see X type of error",
        "code": "Use Y.Z",
        "reason": "Because the taxonomy says...",
        "not_code": "Don't confuse with W.V"
    }}
}}"""

        response = call_llm(self.client, prompt)
        result = extract_json(response)

        if not isinstance(result, dict):
            # Fallback: majority vote
            all_codes = []
            for ann_assignments in assignments.values():
                codes = ann_assignments.get(error_id, [])
                # Flatten nested lists
                if isinstance(codes, list):
                    for c in codes:
                        if isinstance(c, str):
                            all_codes.append(c)
                        elif isinstance(c, list):
                            all_codes.extend([x for x in c if isinstance(x, str)])
                elif isinstance(codes, str):
                    all_codes.append(codes)

            code_counts = Counter(all_codes)
            majority_codes = [c for c, count in code_counts.items()
                            if count >= len(assignments) / 2]

            return {
                "final_codes": majority_codes,
                "reasoning": "Fallback to majority vote",
                "corrections": [],
                "learned_rule": None
            }

        return result


# =============================================================================
# METRICS CALCULATOR (Fleiss' Kappa)
# =============================================================================

class MetricsCalculator:
    """Calculate agreement metrics using Fleiss' Kappa."""

    @staticmethod
    def compute_fleiss_kappa(assignments: Dict, typed_errors: List[Dict],
                             taxonomy: Dict) -> Tuple[float, Dict]:
        """
        Compute Fleiss' Kappa for inter-annotator agreement.
        """
        if not assignments or len(assignments) < 2 or not typed_errors:
            return 0.0, {}

        all_taxonomy_codes = set()
        for cat in ['category_a', 'category_b', 'category_c']:
            codes = taxonomy.get(cat, {})
            if isinstance(codes, dict):
                all_taxonomy_codes.update(codes.keys())

        if not all_taxonomy_codes:
            return 0.0, {}

        annotator_ids = list(assignments.keys())
        n_raters = len(annotator_ids)
        n_subjects = len(typed_errors)

        if n_subjects == 0 or n_raters < 2:
            return 0.0, {}

        per_code_kappa = {}

        for code in all_taxonomy_codes:
            ratings = []

            for error in typed_errors:
                error_id = error.get('error_id', '')
                row = []
                for ann_id in annotator_ids:
                    ann_codes = assignments.get(ann_id, {}).get(error_id, [])
                    # Flatten nested lists
                    flat_codes = []
                    if isinstance(ann_codes, list):
                        for c in ann_codes:
                            if isinstance(c, str):
                                flat_codes.append(c)
                            elif isinstance(c, list):
                                flat_codes.extend([x for x in c if isinstance(x, str)])
                    elif isinstance(ann_codes, str):
                        flat_codes = [ann_codes]
                    row.append(1 if code in flat_codes else 0)
                ratings.append(row)

            kappa = MetricsCalculator._fleiss_kappa_binary(ratings, n_raters)
            if kappa is not None:
                per_code_kappa[code] = kappa

        used_codes = set()
        for ann_assignments in assignments.values():
            for codes in ann_assignments.values():
                if isinstance(codes, list):
                    # Flatten and filter to strings only
                    for c in codes:
                        if isinstance(c, str):
                            used_codes.add(c)
                        elif isinstance(c, list):
                            # Handle nested lists
                            for nested in c:
                                if isinstance(nested, str):
                                    used_codes.add(nested)
                elif isinstance(codes, str):
                    used_codes.add(codes)

        relevant_kappas = [k for code, k in per_code_kappa.items() if code in used_codes]

        if not relevant_kappas:
            relevant_kappas = list(per_code_kappa.values())

        macro_kappa = sum(relevant_kappas) / len(relevant_kappas) if relevant_kappas else 0.0

        return macro_kappa, per_code_kappa

    @staticmethod
    def _fleiss_kappa_binary(ratings: List[List[int]], n_raters: int) -> Optional[float]:
        """Compute Fleiss' Kappa for binary ratings."""
        n_subjects = len(ratings)
        if n_subjects == 0 or n_raters < 2:
            return None

        P_i_sum = 0.0
        category_totals = [0, 0]

        for row in ratings:
            n_0 = sum(1 for r in row if r == 0)
            n_1 = sum(1 for r in row if r == 1)

            category_totals[0] += n_0
            category_totals[1] += n_1

            P_i = (n_0 * n_0 + n_1 * n_1 - n_raters) / (n_raters * (n_raters - 1))
            P_i_sum += P_i

        P_bar = P_i_sum / n_subjects if n_subjects > 0 else 0.0

        total_assignments = n_subjects * n_raters
        if total_assignments == 0:
            return None

        p_0 = category_totals[0] / total_assignments
        p_1 = category_totals[1] / total_assignments

        P_e = p_0 * p_0 + p_1 * p_1

        if P_e >= 1.0:
            return 1.0 if P_bar >= 1.0 else None

        kappa = (P_bar - P_e) / (1 - P_e)
        return max(-1.0, min(1.0, kappa))

    @staticmethod
    def compute_reconciliation_agreement(reconciliation_result: Dict) -> float:
        """Compute agreement during error reconciliation."""
        votes = reconciliation_result.get('votes', {})
        all_errors = reconciliation_result.get('all_proposals', [])

        if not votes or not all_errors:
            return 1.0  # No errors, perfect agreement

        # For each error, calculate what fraction of annotators agreed
        agreements = []
        for error in all_errors:
            accept_count = error.get('accept_votes', 0)
            total = len(votes)

            # Agreement is how close to unanimous (all accept or all reject)
            accept_ratio = accept_count / total if total > 0 else 0
            agreement = max(accept_ratio, 1 - accept_ratio)  # Closer to 0 or 1 is better
            agreements.append(agreement)

        return sum(agreements) / len(agreements) if agreements else 1.0


# =============================================================================
# COVERAGE TRACKER
# =============================================================================

class CoverageTracker:
    """Track taxonomy coverage."""

    def __init__(self):
        self.total_errors = 0
        self.coded_errors = 0
        self.codes_used = Counter()
        self.all_taxonomy_codes: Set[str] = set()

    def set_taxonomy_codes(self, taxonomy: Dict):
        """Set the universe of codes from taxonomy."""
        self.all_taxonomy_codes = set()
        if not isinstance(taxonomy, dict):
            return
        for cat in ['category_a', 'category_b', 'category_c']:
            codes = taxonomy.get(cat, {})
            if isinstance(codes, dict):
                self.all_taxonomy_codes.update(codes.keys())

    def record(self, consolidated_codes: Dict):
        """Record codes from consolidated assignments."""
        for error_id, codes in consolidated_codes.items():
            self.total_errors += 1

            if not isinstance(codes, list):
                codes = [codes] if codes else []

            real_codes = [c for c in codes if isinstance(c, str) and c]
            if real_codes:
                self.coded_errors += 1
                for c in real_codes:
                    self.codes_used[c] += 1

    def get_coverage(self) -> float:
        """Get coverage score."""
        if self.total_errors == 0:
            return 0.0
        return self.coded_errors / self.total_errors

    def get_code_distribution(self) -> Dict:
        """Get distribution of code usage."""
        return dict(self.codes_used.most_common())


# =============================================================================
# TAXONOMY REFINER
# =============================================================================

class TaxonomyRefiner:
    """Refine taxonomy based on round data."""

    def __init__(self, client: TextProvider, shared_kb: SharedKnowledgeBase):
        self.client = client
        self.shared_kb = shared_kb

    def refine(self, taxonomy: Dict, round_data: Dict) -> Dict:
        """Refine taxonomy based on disagreements and learned rules."""
        progress("Taxonomy Refinement")

        rule_count = self.shared_kb.consolidate_rules()
        progress(f"  Consolidated to {rule_count} unique rules")

        kappa = round_data.get('kappa', 0)
        learned_rules = round_data.get('learned_rules', [])
        low_agreement_codes = round_data.get('low_agreement_codes', [])

        changes = []

        if kappa >= Config.KAPPA_TARGET:
            progress(f"  Kappa {kappa:.3f} >= target, minimal refinement")
            return {"taxonomy": taxonomy, "changes": []}

        if learned_rules:
            progress(f"  Applying {len(learned_rules)} learned rules")
            for rule in learned_rules[:5]:
                taxonomy, rule_changes = self._apply_rule(taxonomy, rule)
                changes.extend(rule_changes)

        if low_agreement_codes:
            progress(f"  Clarifying {len(low_agreement_codes)} low-agreement codes")
            for code_id in low_agreement_codes[:3]:
                taxonomy, code_changes = self._clarify_code(taxonomy, code_id)
                changes.extend(code_changes)

        progress(f"  Made {len(changes)} changes")
        return {"taxonomy": taxonomy, "changes": changes}

    def _apply_rule(self, taxonomy: Dict, rule: Dict) -> Tuple[Dict, List[Dict]]:
        """Apply a learned rule to improve taxonomy."""
        changes = []

        if not isinstance(rule, dict):
            return taxonomy, changes

        code = rule.get('code', '')
        reason = rule.get('reason', '')

        for cat in ['category_a', 'category_b', 'category_c']:
            if code in taxonomy.get(cat, {}):
                code_data = taxonomy[cat][code]
                if isinstance(code_data, dict):
                    current = code_data.get('when_to_use', '')
                    new_guidance = f"{current} {reason}".strip()
                    taxonomy[cat][code]['when_to_use'] = new_guidance[:500]

                    changes.append({
                        "action": "added_guidance",
                        "code": code,
                        "guidance": reason[:100]
                    })

        return taxonomy, changes

    def _clarify_code(self, taxonomy: Dict, code_id: str) -> Tuple[Dict, List[Dict]]:
        """Clarify a code that has low agreement."""
        changes = []

        for cat in ['category_a', 'category_b', 'category_c']:
            if code_id in taxonomy.get(cat, {}):
                code_data = taxonomy[cat][code_id]
                if not isinstance(code_data, dict):
                    continue

                prompt = f"""This taxonomy code has low inter-annotator agreement:

Code: {code_id}
Name: {code_data.get('name', '?')}
Definition: {code_data.get('definition', '')}

Suggest a clearer, more specific definition that would reduce ambiguity.
Focus on observable criteria that annotators can check.

OUTPUT JSON:
{{
    "improved_definition": "...",
    "when_to_use": "Specific conditions when this code applies",
    "when_not_to_use": "Conditions when this code should NOT be used"
}}"""

                response = call_llm(self.client, prompt)
                result = extract_json(response)

                if isinstance(result, dict) and result.get('improved_definition'):
                    taxonomy[cat][code_id]['definition'] = result['improved_definition']

                    if result.get('when_to_use'):
                        taxonomy[cat][code_id]['when_to_use'] = result['when_to_use']
                    if result.get('when_not_to_use'):
                        taxonomy[cat][code_id]['when_not_to_use'] = result['when_not_to_use']

                    changes.append({
                        "action": "clarified",
                        "code": code_id
                    })

        return taxonomy, changes


# =============================================================================
# EARLY STOPPING CONTROLLER
# =============================================================================

class EarlyStoppingController:
    """Control early stopping based on metrics stability."""

    def __init__(self):
        self.kappa_history: List[float] = []
        self.coverage_history: List[float] = []

    def record(self, kappa: float, coverage: float):
        self.kappa_history.append(kappa)
        self.coverage_history.append(coverage)

    def should_stop(self) -> Tuple[bool, str]:
        """Determine if we should stop early."""
        if Config.NO_EARLY_STOP:
            return False, "Early stopping disabled"

        if len(self.kappa_history) < 3:
            return False, "Need more rounds"

        recent_kappa = self.kappa_history[-3:]
        kappa_stable = all(k >= Config.KAPPA_TARGET for k in recent_kappa)
        kappa_variance = max(recent_kappa) - min(recent_kappa)

        recent_coverage = self.coverage_history[-3:]
        coverage_ok = all(c >= Config.COVERAGE_FLOOR for c in recent_coverage)

        if kappa_stable and kappa_variance < 0.05 and coverage_ok:
            return True, f"Kappa stable >= {Config.KAPPA_TARGET}"

        if len(self.kappa_history) >= 5:
            early_avg = sum(self.kappa_history[:2]) / 2
            recent_avg = sum(self.kappa_history[-2:]) / 2
            if recent_avg <= early_avg + 0.02:
                return True, "Kappa not improving"

        return False, "Continue"


# =============================================================================
# MAIN PIPELINE
# =============================================================================

class TaxonomyRefinerPipeline:
    """
    MATRS v14.1 - Main refinement pipeline with deliberative phases.
    """

    def __init__(
        self,
        taxonomy_path: Path,
        traces_path: Path,
        output_dir: Path,
        client: TextProvider | None = None,
    ):
        self.client = client or create_provider(Config.PROVIDER, Config.MODEL)

        self.taxonomy_path = Path(taxonomy_path).resolve()
        self.traces_path = Path(traces_path).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.taxonomy = self._load_taxonomy()
        self.traces = self._load_traces()

        self.shared_kb = SharedKnowledgeBase()
        self.annotator_memories = self._create_annotators()

        # Initialize components (new phases)
        self.trace_parser = TraceParser()
        self.trace_sampler = StratifiedTraceSampler(self.traces)

        self.error_discovery = ErrorDiscovery(
            self.client, self.annotator_memories, self.shared_kb
        )
        self.error_reconciliation = ErrorReconciliation(
            self.client, self.annotator_memories, self.shared_kb
        )
        self.failure_typing = FailureTyping(
            self.client, self.annotator_memories, self.shared_kb
        )
        self.code_assignment = CodeAssignment(
            self.client, self.annotator_memories, self.shared_kb
        )
        self.code_deliberation = CodeDeliberation(
            self.client, self.annotator_memories, self.shared_kb
        )

        self.refiner = TaxonomyRefiner(self.client, self.shared_kb)
        self.early_stopper = EarlyStoppingController()

        self.kappa_history: List[float] = []
        self.coverage_history: List[float] = []

    def _create_annotators(self) -> List[AnnotatorMemory]:
        """Create 4 identical annotators."""
        return [AnnotatorMemory(ann_id) for ann_id in ANNOTATOR_IDS]

    def run(self) -> Dict:
        """Run the full refinement pipeline."""
        if not self.taxonomy or not self.traces:
            progress("ERROR: Missing taxonomy or traces")
            return {}

        progress("=" * 70)
        progress("MATRS v14.1 - Multi-Agent Taxonomy Refinement System")
        progress("=" * 70)
        progress(f"Model: {Config.MODEL}")
        progress(f"Annotators: {', '.join(ANNOTATOR_IDS)} (identical at start)")
        progress(f"Max rounds: {Config.MAX_ROUNDS}")
        progress(f"Traces per round: {Config.TRACES_PER_ROUND}")
        progress(f"Loaded taxonomy with {self._count_codes()} codes")
        progress(f"Loaded {len(self.traces)} traces")
        progress("")
        progress("Pipeline Phases:")
        progress("  1. Error Discovery (independent)")
        progress("  2. Error Reconciliation (deliberative)")
        progress("  3. Failure Typing (A/B/C)")
        progress("  4. Code Assignment (with validation)")
        progress("  5. Code Deliberation (for disagreements)")

        # Detect overlapping codes in taxonomy (helps with disambiguation)
        progress("\nAnalyzing taxonomy for overlapping codes...")
        self.shared_kb.detect_overlapping_codes(self.taxonomy, self.client)

        # Run calibration
        self._run_calibration()

        # Run rounds
        for round_num in range(1, Config.MAX_ROUNDS + 1):
            progress(f"\n{'=' * 50}")
            progress(f"ROUND {round_num}/{Config.MAX_ROUNDS}")

            round_result = self._run_round(round_num)

            kappa = round_result.get('kappa', 0)
            coverage = round_result.get('coverage', 0)

            self.kappa_history.append(kappa)
            self.coverage_history.append(coverage)
            self.early_stopper.record(kappa, coverage)

            progress(f"  Reconciliation Agreement: {round_result.get('reconciliation_agreement', 0):.3f}")
            progress(f"  Phase 2 Kappa: {kappa:.3f}")
            progress(f"  Coverage: {coverage:.3f}")
            progress(f"  Errors identified: {round_result.get('total_errors', 0)}")

            self._save_step(f"round_{round_num}", round_result)

            should_stop, reason = self.early_stopper.should_stop()
            if should_stop:
                progress(f"\nEarly stopping: {reason}")
                break

            if kappa < Config.KAPPA_TARGET:
                refine_result = self.refiner.refine(self.taxonomy, round_result)
                self.taxonomy = refine_result.get('taxonomy', self.taxonomy)
                self._save_step(f"refined_round_{round_num}", refine_result)

        self._save_final()

        return {
            "final_kappa": self.kappa_history[-1] if self.kappa_history else 0,
            "final_coverage": self.coverage_history[-1] if self.coverage_history else 0,
            "rounds_completed": len(self.kappa_history),
            "learned_rules": len(self.shared_kb.learned_rules),
            "anchor_examples": len(self.shared_kb.anchor_examples)
        }

    def _run_calibration(self):
        """Run calibration phase."""
        progress("\n" + "=" * 50)
        progress("Calibration Phase")

        calibration_traces = self.traces[:Config.CALIBRATION_TRACES]

        for trace in calibration_traces:
            try:
                parsed = self.trace_parser.parse(trace)

                # Phase 1: Discovery
                discovery_result = self.error_discovery.discover_errors(parsed, self.taxonomy)

                # Phase 2: Reconciliation
                reconciliation_result = self.error_reconciliation.reconcile(
                    discovery_result, parsed
                )

                agreed_errors = reconciliation_result.get('agreed_errors', [])

                if agreed_errors:
                    # Phase 3: Typing
                    typing_result = self.failure_typing.type_failures(
                        agreed_errors, parsed, self.taxonomy
                    )
                    typed_errors = typing_result.get('typed_errors', [])

                    # Phase 4: Code Assignment
                    assignment_result = self.code_assignment.assign_codes(
                        typed_errors, parsed, self.taxonomy
                    )

                    # Add as anchor
                    self.shared_kb.add_anchor(
                        parsed.get('problem_id', 'unknown'),
                        typed_errors,
                        list(assignment_result.get('consolidated', {}).values()),
                        "Calibration example"
                    )
            except Exception as e:
                progress(f"  [!] Calibration error: {e}")

        progress(f"  Calibration complete with {len(self.shared_kb.anchor_examples)} anchors")

    def _run_round(self, round_num: int) -> Dict:
        """Run a single annotation round with all phases."""
        round_traces = self.trace_sampler.sample(Config.TRACES_PER_ROUND, round_num)

        all_results = []
        coverage_tracker = CoverageTracker()
        coverage_tracker.set_taxonomy_codes(self.taxonomy)

        reconciliation_agreements = []
        all_assignments = {}
        all_typed_errors = []
        learned_rules = []
        low_agreement_codes = []

        for trace in round_traces:
            try:
                parsed = self.trace_parser.parse(trace)
                trace_id = parsed.get('problem_id', 'unknown')

                # ========== PHASE 1: ERROR DISCOVERY ==========
                discovery_result = self.error_discovery.discover_errors(parsed, self.taxonomy)

                # ========== PHASE 2: ERROR RECONCILIATION ==========
                reconciliation_result = self.error_reconciliation.reconcile(
                    discovery_result, parsed
                )

                reconciliation_agreement = MetricsCalculator.compute_reconciliation_agreement(
                    reconciliation_result
                )
                reconciliation_agreements.append(reconciliation_agreement)

                agreed_errors = reconciliation_result.get('agreed_errors', [])

                if not agreed_errors:
                    all_results.append({
                        "trace_id": trace_id,
                        "agreed_errors": [],
                        "typed_errors": [],
                        "assignments": {},
                        "consolidated": {}
                    })
                    continue

                # ========== PHASE 3: FAILURE TYPING ==========
                typing_result = self.failure_typing.type_failures(
                    agreed_errors, parsed, self.taxonomy
                )
                typed_errors = typing_result.get('typed_errors', [])
                all_typed_errors.extend(typed_errors)

                # ========== PHASE 4: CODE ASSIGNMENT ==========
                assignment_result = self.code_assignment.assign_codes(
                    typed_errors, parsed, self.taxonomy
                )

                assignments = assignment_result.get('assignments', {})
                consolidated = assignment_result.get('consolidated', {})
                agreement_matrix = assignment_result.get('agreement_matrix', {})

                # Collect for Kappa
                for ann_id, ann_assignments in assignments.items():
                    if ann_id not in all_assignments:
                        all_assignments[ann_id] = {}
                    for error_id, codes in ann_assignments.items():
                        full_error_id = f"{trace_id}_{error_id}"
                        all_assignments[ann_id][full_error_id] = codes

                # ========== PHASE 5: CODE DELIBERATION ==========
                deliberation_result = self.code_deliberation.deliberate(
                    typed_errors, assignments, agreement_matrix, self.taxonomy
                )

                # Update consolidated with deliberation results
                for error_id, final_codes in deliberation_result.get('final_assignments', {}).items():
                    consolidated[error_id] = final_codes

                # Collect learned rules
                learned_rules.extend(deliberation_result.get('learned_rules', []))

                # Track low agreement codes
                for error_id, matrix_data in agreement_matrix.items():
                    if matrix_data.get('agreement_level', 0) < 0.5:
                        low_agreement_codes.extend(consolidated.get(error_id, []))

                # Record coverage
                coverage_tracker.record(consolidated)

                # Add anchor if high agreement
                avg_agreement = sum(m.get('agreement_level', 0)
                                   for m in agreement_matrix.values()) / len(agreement_matrix) if agreement_matrix else 0
                if avg_agreement > 0.8:
                    self.shared_kb.add_anchor(
                        trace_id,
                        typed_errors,
                        [c for codes in consolidated.values() for c in codes],
                        f"High agreement ({avg_agreement:.2f})"
                    )

                all_results.append({
                    "trace_id": trace_id,
                    "agreed_errors": agreed_errors,
                    "typed_errors": typed_errors,
                    "assignments": assignments,
                    "consolidated": consolidated,
                    "agreement_matrix": agreement_matrix,
                    "deliberation": deliberation_result
                })

            except Exception as e:
                progress(f"  [!] Error processing trace: {e}")
                continue

        # Calculate metrics
        all_errors_for_kappa = [
            {"error_id": f"{r['trace_id']}_{e['error_id']}"}
            for r in all_results
            for e in r.get('typed_errors', [])
        ]

        kappa, per_code_kappa = MetricsCalculator.compute_fleiss_kappa(
            all_assignments, all_errors_for_kappa, self.taxonomy
        )
        coverage = coverage_tracker.get_coverage()
        reconciliation_agreement = sum(reconciliation_agreements) / len(reconciliation_agreements) if reconciliation_agreements else 0

        # Update annotator summaries
        for memory in self.annotator_memories:
            memory.add_round_summary(
                f"Round {round_num}: Kappa={kappa:.3f}, Coverage={coverage:.3f}, "
                f"Errors={len(all_typed_errors)}, Rules={len(learned_rules)}"
            )

        return {
            "round": round_num,
            "traces_processed": len(round_traces),
            "reconciliation_agreement": reconciliation_agreement,
            "kappa": kappa,
            "coverage": coverage,
            "total_errors": len(all_typed_errors),
            "learned_rules": learned_rules,
            "low_agreement_codes": list(set(low_agreement_codes)),
            "results": all_results,
            "code_distribution": coverage_tracker.get_code_distribution()
        }

    def _load_taxonomy(self) -> Dict:
        """Load taxonomy from file."""
        progress(f"Loading taxonomy from: {self.taxonomy_path}")

        if not self.taxonomy_path.exists():
            progress("  [!] Taxonomy file does not exist")
            return {}

        try:
            content = self.taxonomy_path.read_text(encoding='utf-8')
            taxonomy = json.loads(content)

            code_count = sum(len(taxonomy.get(cat, {}))
                           for cat in ['category_a', 'category_b', 'category_c'])
            if code_count == 0:
                progress("  [!] Taxonomy has no codes")
                return {}

            progress(f"  Loaded taxonomy with {code_count} codes")
            return taxonomy
        except Exception as e:
            progress(f"  [!] Error loading taxonomy: {e}")
            return {}

    def _load_traces(self) -> List[Dict]:
        """Load traces from file or directory."""
        progress(f"Loading traces from: {self.traces_path}")
        traces = load_traces(self.traces_path)
        progress(f"  Successfully normalized {len(traces)} traces")
        return traces

    def _is_tau_bench_trace(self, item: Dict) -> bool:
        """Check if a trace item is in tau_bench format."""
        return (isinstance(item, dict)
                and 'traj' in item
                and 'task_id' in item
                and 'reward' in item
                and isinstance(item.get('traj'), list))

    def _convert_tau_bench_trace(self, item: Dict, index: int, file_stem: str) -> Dict:
        """Convert a tau_bench trace to normalized format for the refiner pipeline."""
        task_id = item.get('task_id', index)
        trial = item.get('trial', 0)
        reward = item.get('reward', 0.0)
        info = item.get('info', {})
        task_info = info.get('task', {})
        traj = item.get('traj', [])

        task = task_info.get('instruction', '')

        # Determine domain from filename
        domain = 'unknown'
        file_lower = file_stem.lower()
        if 'airline' in file_lower:
            domain = 'airline'
        elif 'retail' in file_lower:
            domain = 'retail'

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

        problem_id = f"tau_bench_{domain}_{task_id}_trial{trial}"

        return {
            'problem_id': problem_id,
            'traj': traj,
            'reward': reward,
            'final_verdict': 'ACCEPT' if reward == 1.0 else 'REJECT',
            'raw_trajectory': '\n\n'.join(trajectory_parts),
            'task': task[:500] if task else '',
            '_format': 'tau_bench',
            'metadata': {
                'domain': domain,
                'task_id': task_id,
                'trial': trial
            }
        }

    def _convert_codex_session(self, entries: List[Dict], file_path: Path) -> Optional[Dict]:
        """Convert a Codex CLI session.jsonl into a standard trace."""
        task = ""
        llm_name = "unknown"
        trajectory_parts = []
        parent_name = file_path.parent.name.lower()

        for entry in entries:
            entry_type = entry.get('type', '')
            payload = entry.get('payload', {})

            if entry_type == 'session_meta':
                instructions = payload.get('instructions', '')
                task_match = re.search(
                    r'\*\*Task\*\*:\s*\n(.*?)(?:\n====|\n---|\n\*\*)',
                    instructions, re.DOTALL
                )
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

        problem_id = f"codex_{parent_name}_{file_path.stem}"
        return {
            'problem_id': problem_id,
            'task': task,
            'raw_trajectory': '\n\n'.join(trajectory_parts),
            '_format': 'codex_session',
            'metadata': {
                'mas_name': 'codex_session',
                'llm_name': llm_name,
                'benchmark_name': 'sre',
                'trace_id': file_path.stem
            }
        }

    def _is_mad_trace(self, item: Dict) -> bool:
        """Check if a trace item is in MAD (Multi-Agent Dataset) format."""
        return (isinstance(item, dict)
                and 'mas_name' in item
                and 'trace' in item
                and isinstance(item.get('trace'), dict)
                and 'trajectory' in item.get('trace', {}))

    def _convert_mad_trace(self, item: Dict, index: int) -> Dict:
        """Convert a MAD format trace to normalized format."""
        mas_name = item.get('mas_name', 'unknown')
        llm_name = item.get('llm_name', 'unknown')
        benchmark = item.get('benchmark_name', 'unknown')
        trace_id = item.get('trace_id', index)

        trace_data = item.get('trace', {})
        trajectory = trace_data.get('trajectory', '')

        # Extract task from trajectory
        task = ""
        task_match = re.search(
            r'task_prompt.*?[:|]\s*(.*?)(?:\n\*\*|\n\n|\n\|)',
            trajectory, re.IGNORECASE | re.DOTALL
        )
        if task_match:
            task = task_match.group(1).strip()[:500]

        problem_id = f"{mas_name}_{benchmark}_{trace_id}"

        return {
            'problem_id': problem_id,
            'task': task,
            'raw_trajectory': trajectory,
            'final_verdict': 'INCOMPLETE',  # MAD traces don't have explicit verdict
            '_format': 'mad',
            'metadata': {
                'mas_name': mas_name,
                'llm_name': llm_name,
                'benchmark_name': benchmark,
                'trace_id': trace_id,
                'mast_annotation': item.get('mast_annotation', {})
            }
        }

    def _load_trace_file(self, path: Path) -> List[Dict]:
        """Load a single trace file."""
        try:
            content = path.read_text(encoding='utf-8')

            try:
                data = json.loads(content)
                if isinstance(data, list):
                    # Check if this is a tau_bench file
                    if len(data) > 0 and self._is_tau_bench_trace(data[0]):
                        return [self._convert_tau_bench_trace(item, i, path.stem)
                                for i, item in enumerate(data)]
                    # Check if this is a MAD format file
                    if len(data) > 0 and self._is_mad_trace(data[0]):
                        return [self._convert_mad_trace(item, i)
                                for i, item in enumerate(data)]
                    return data
                if isinstance(data, dict):
                    # Check single tau_bench trace
                    if self._is_tau_bench_trace(data):
                        return [self._convert_tau_bench_trace(data, 0, path.stem)]
                    # Check single MAD trace
                    if self._is_mad_trace(data):
                        return [self._convert_mad_trace(data, 0)]
                    return [data]
            except json.JSONDecodeError:
                pass

            # Parse JSONL line by line
            is_codex_session = False
            codex_entries = []
            events = []
            problem_id = path.stem
            raw_trajectory = []

            for line in content.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)

                    # Detect Codex CLI session format
                    if entry.get('type') in ('session_meta', 'response_item', 'turn_context', 'event_msg'):
                        is_codex_session = True
                        codex_entries.append(entry)
                        continue

                    events.append(entry)
                    if 'problem_id' in entry:
                        problem_id = entry['problem_id']
                    if entry.get('event') == 'completion_received':
                        data = entry.get('data', {})
                        agent = entry.get('agent', '')
                        content_text = data.get('content', '')[:500] if isinstance(data, dict) else ''
                        raw_trajectory.append(f"[{agent}] {content_text}")
                except json.JSONDecodeError:
                    continue

            if is_codex_session and codex_entries:
                trace = self._convert_codex_session(codex_entries, path)
                return [trace] if trace else []

            if events:
                return [{
                    "problem_id": problem_id,
                    "events": events,
                    "raw_trajectory": '\n'.join(raw_trajectory)
                }]

            return []
        except Exception:
            return []

    def _count_codes(self) -> int:
        """Count total codes in taxonomy."""
        count = 0
        for cat in ['category_a', 'category_b', 'category_c']:
            codes = self.taxonomy.get(cat, {})
            if isinstance(codes, dict):
                count += len(codes)
        return count

    def _save_step(self, name: str, data: Dict):
        """Save intermediate step."""
        path = self.output_dir / f"{name}.json"
        save_json(data, path)

    def _save_final(self):
        """Save final outputs."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        save_json(self.taxonomy, self.output_dir / f"taxonomy_refined_{timestamp}.json")

        save_json({
            "learned_rules": self.shared_kb.learned_rules,
            "anchor_examples": self.shared_kb.anchor_examples,
            "code_usage": dict(self.shared_kb.code_usage_stats),
            "confusion_pairs": [
                {"codes": list(pair), "count": count}
                for pair, count in self.shared_kb.confusion_pairs.most_common(20)
            ],
            "overlapping_codes": self.shared_kb.overlapping_codes,
            "disambiguation_rules": self.shared_kb.disambiguation_rules
        }, self.output_dir / f"shared_knowledge_{timestamp}.json")

        for memory in self.annotator_memories:
            save_json({
                "annotator_id": memory.annotator_id,
                "annotation_count": len(memory.annotation_history),
                "corrections": memory.correction_log,
                "common_codes": dict(memory.my_common_codes.most_common(10)),
                "disagreements": memory.my_disagreements[-10:] if memory.my_disagreements else []
            }, self.output_dir / f"annotator_{memory.annotator_id}_{timestamp}.json")

        # Get top confusion pairs for summary
        top_confusions = self.shared_kb.get_top_confusion_pairs(5)

        save_json({
            "final_kappa": self.kappa_history[-1] if self.kappa_history else 0,
            "final_coverage": self.coverage_history[-1] if self.coverage_history else 0,
            "kappa_history": self.kappa_history,
            "coverage_history": self.coverage_history,
            "rounds_completed": len(self.kappa_history),
            "learned_rules_count": len(self.shared_kb.learned_rules),
            "anchor_examples_count": len(self.shared_kb.anchor_examples),
            "overlapping_codes_detected": len(self.shared_kb.overlapping_codes),
            "top_confusion_pairs": [
                {"code1": c1, "code2": c2, "disagreements": count}
                for c1, c2, count in top_confusions
            ]
        }, self.output_dir / f"summary_{timestamp}.json")

        progress("\n" + "=" * 70)
        progress("REFINEMENT COMPLETE")
        progress("=" * 70)
        progress(f"Final Kappa: {self.kappa_history[-1] if self.kappa_history else 0:.3f}")
        progress(f"Final Coverage: {self.coverage_history[-1] if self.coverage_history else 0:.3f}")
        progress(f"Rounds: {len(self.kappa_history)}")
        progress(f"Learned Rules: {len(self.shared_kb.learned_rules)}")
        progress(f"Anchor Examples: {len(self.shared_kb.anchor_examples)}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="MATRS v14.1 - Taxonomy Refinement")
    parser.add_argument("--taxonomy", required=True, help="Path to taxonomy JSON")
    parser.add_argument("--traces", required=True, help="Path to traces file/directory")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "google", "bedrock"],
        default=os.getenv("ADAMAST_PROVIDER"),
        help="Model API provider",
    )
    parser.add_argument("--model", default=None, help="Model to use")
    parser.add_argument("--max-rounds", type=int, default=5, help="Max rounds")
    parser.add_argument("--no-early-stop", action="store_true", help="Disable early stopping")

    args = parser.parse_args()

    Config.PROVIDER = args.provider
    Config.MODEL = resolve_model(args.provider, args.model)
    Config.MAX_ROUNDS = args.max_rounds
    Config.NO_EARLY_STOP = args.no_early_stop

    pipeline = TaxonomyRefinerPipeline(
        Path(args.taxonomy),
        Path(args.traces),
        Path(args.output)
    )

    result = pipeline.run()

    if result:
        progress("\nPipeline completed successfully")
    else:
        progress("\nPipeline failed")


if __name__ == "__main__":
    main()
