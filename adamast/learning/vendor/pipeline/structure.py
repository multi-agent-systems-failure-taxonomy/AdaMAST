"""Trace structure extraction orchestration.

Model-facing structure and role-classification instructions live in
``vendor/adamast/pipeline/assets``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

from adamast.learning.vendor.config import PipelineConfig
from adamast.learning.vendor.llm import LLMClient, extract_json
from adamast.learning.vendor.pipeline.prompts import DEFAULT_ROLE_DEFINITIONS, render_prompt_asset
from adamast.learning.vendor.utils import format_trace_for_prompt, progress, stratified_sample, get_trajectory_text


# Patterns that reliably indicate agent names within trajectories.
_HIGH_PRECISION_PATTERNS = [
    r"\|\s*\*\*(?:assistant_role_name|user_role_name)\*\*\s*\|\s*([^|]+?)\s*\|",
    r"\b(Agent_[A-Za-z_]+(?:__[A-Za-z_]+)?)\b",
    r"===\s*([A-Z][A-Z_]+)\s*===",
]

_KNOWN_AGENT_TITLES = [
    "Officer", "Programmer", "Engineer", "Reviewer", "Tester",
    "Designer", "Counselor", "Architect", "Manager", "Verifier",
]
_TITLE_PATTERN = r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:" + "|".join(_KNOWN_AGENT_TITLES) + r"))\b"

# Common structural words that the high-precision patterns can match
# but which are not agents.
_NON_AGENT_MARKERS = {
    "TRACE", "ERROR", "TASK", "META", "SYSTEM", "OUTPUT",
    "INPUT", "RESULT", "END", "START",
}

_TOOL_CALL_PATTERNS = [
    re.compile(r"\[(?:ASSISTANT )?TOOL CALL\]", re.IGNORECASE),
    re.compile(r"\[TOOL RESPONSE", re.IGNORECASE),
    re.compile(r'"tool_calls"\s*:', re.IGNORECASE),
    re.compile(r'"function"\s*:\s*\{', re.IGNORECASE),
    re.compile(r"Action:\s*\w+\[", re.IGNORECASE),
    re.compile(r"<tool_call>", re.IGNORECASE),
]
_CODE_EXEC_PATTERNS = [
    re.compile(r"```(?:python|bash|javascript|shell)", re.IGNORECASE),
    re.compile(r"exec(?:ute)?_code|run_code|code_interpreter", re.IGNORECASE),
    re.compile(r"IPython\.display|subprocess\.run", re.IGNORECASE),
]
_WEB_PATTERNS = [
    re.compile(r"browse|web_search|search_google|fetch_url", re.IGNORECASE),
]
_TOOL_NAME_PATTERNS = [
    re.compile(r"\[(?:ASSISTANT )?TOOL CALL\]\s*(\w+)\(", re.IGNORECASE),
    re.compile(r'"name"\s*:\s*"(\w+)"', re.IGNORECASE),
    re.compile(r"Action:\s*(\w+)\[", re.IGNORECASE),
]


class TraceStructureExtractor:
    """Discover the system architecture, agents, roles, and trace format."""

    def __init__(self, client: LLMClient, config: PipelineConfig):
        self.client = client
        self.config = config

    def extract(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        progress("Step 2: Trace Structure Extractor")

        progress("  Extracting agents from trajectories...")
        agents = self._extract_agents(traces)
        progress(f"  Found {len(agents)} unique agents via pattern matching")

        progress("  Classifying agent roles via LLM...")
        agent_to_role, role_details = self._classify_roles(agents, traces)
        for role, details in role_details.items():
            if details["agents"]:
                progress(f"    {role}: {len(details['agents'])} agents - {details['agents'][:3]}")

        progress("  Detecting agent capabilities...")
        capabilities = self._detect_capabilities(traces)
        progress(f"    Interaction style: {capabilities['interaction_style']}")
        if capabilities["tool_names_seen"]:
            progress(f"    Tools seen: {capabilities['tool_names_seen'][:10]}")

        sample = stratified_sample(traces, self.config.traces_for_analysis)
        traces_text = "\n\n".join(format_trace_for_prompt(t, max_length=4000) for t in sample)
        agents_list = list(agents)[:30]
        role_defs_text = "\n".join(
            f"- {role}: {details.get('definition', 'N/A')}"
            for role, details in role_details.items()
            if details.get("agents")
        )

        prompt = render_prompt_asset(
            "trace_structure.md",
            agents_list=agents_list,
            agent_to_role=agent_to_role,
            role_defs_text=role_defs_text,
            traces_text=traces_text,
        )

        llm_result = extract_json(self.client.chat(prompt))

        # Apply LLM-suggested role corrections.
        for agent, corrected in (llm_result.get("agent_role_corrections", {}) or {}).items():
            if agent in agent_to_role and agent_to_role[agent] != corrected:
                old = agent_to_role[agent]
                progress(f"    Correcting {agent}: {old} -> {corrected}")
                if old in role_details and agent in role_details[old].get("agents", []):
                    role_details[old]["agents"].remove(agent)
                if corrected not in role_details:
                    role_details[corrected] = {
                        "agents": [],
                        "definition": f"Agent that performs {corrected} functions.",
                        "purpose": f"Perform {corrected} tasks",
                    }
                role_details[corrected]["agents"].append(agent)
                agent_to_role[agent] = corrected

        llm_arch = llm_result.get("architecture") or {}
        if not llm_arch.get("topology"):
            progress("  [!] LLM returned empty architecture — constructing from discovered agents")
            llm_arch = self._fallback_architecture(role_details, capabilities)

        llm_format = llm_result.get("trace_format") or {}
        if not llm_format.get("key_fields"):
            progress("  [!] LLM returned empty trace_format — constructing from trace content")
            llm_format = self._fallback_trace_format(traces[:5])

        progress(f"  Trace format: {len(llm_format.get('key_fields', []))} key fields")
        progress(f"  Topology: {llm_arch.get('topology', 'Unknown')}")
        progress(f"  Verification: {llm_arch.get('verification_pattern', 'Unknown')}")

        return {
            "trace_format": llm_format,
            "architecture": llm_arch,
            "discovered_agents": {
                "agents": list(agents),
                "agent_to_role": agent_to_role,
                "role_details": role_details,
            },
            "capabilities": capabilities,
        }

    # ───── Agent extraction ─────

    def _extract_agents(self, traces: List[Dict[str, Any]]) -> Set[str]:
        agents: Set[str] = set()

        for trace in traces:
            metadata = trace.get("metadata", {}) or {}

            # Pre-tagged formats can skip extraction entirely.
            fmt = metadata.get("_format")
            if fmt == "tau_bench":
                domain = metadata.get("benchmark_name", "unknown")
                agents.add(f"{domain.title()} Agent")
                continue
            if fmt == "codex_session":
                agents.add("SRE Agent")
                continue

            for agent in metadata.get("agents_seen", []) or []:
                if isinstance(agent, str) and len(agent) > 1:
                    agents.add(agent.strip())

            trajectory = get_trajectory_text(trace)
            if not trajectory:
                continue

            for pattern in _HIGH_PRECISION_PATTERNS:
                for match in re.findall(pattern, trajectory, re.IGNORECASE):
                    name = match.strip()
                    if 1 < len(name) < 50 and name.upper() not in _NON_AGENT_MARKERS:
                        agents.add(name)

            for match in re.findall(_TITLE_PATTERN, trajectory):
                name = match.strip()
                if 3 < len(name) < 50:
                    agents.add(name)

        return _normalize_agents(agents)

    # ───── Role classification ─────

    def _classify_roles(
        self, agents: Set[str], traces: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
        if not agents:
            return {}, {}

        samples = self._gather_agent_samples(agents, traces)

        agents_with_samples = "\n\n".join(
            f"AGENT: {agent}\nBEHAVIOR SAMPLES:\n{sample[:800]}"
            for agent, sample in samples.items()
        )

        default_hint = "\n".join(
            f"  - {role}: {info['definition']}"
            for role, info in DEFAULT_ROLE_DEFINITIONS.items()
        )

        prompt = render_prompt_asset(
            "role_classification.md",
            agents_with_samples=agents_with_samples,
            default_hint=default_hint,
        )

        try:
            agent_roles = extract_json(self.client.chat(prompt)).get("agent_roles", {})
        except Exception as e:  # noqa: BLE001
            progress(f"  [!] LLM role classification failed: {e}")
            agent_roles = {}

        agent_to_role: Dict[str, str] = {}
        role_details: Dict[str, Dict[str, Any]] = {}

        for agent in agents:
            info = agent_roles.get(agent, {}) or {}
            role = info.get("role", "solver")
            definition = info.get("definition", "")
            purpose = info.get("purpose", "")
            agent_to_role[agent] = role

            bucket = role_details.setdefault(role, {
                "agents": [],
                "definition": definition,
                "purpose": purpose,
            })
            bucket["agents"].append(agent)
            if not bucket.get("definition") and definition:
                bucket["definition"] = definition
            if not bucket.get("purpose") and purpose:
                bucket["purpose"] = purpose

        for role, details in role_details.items():
            if not details.get("purpose"):
                default = DEFAULT_ROLE_DEFINITIONS.get(role, {})
                details["purpose"] = default.get("purpose", f"Perform {role} tasks")
            if not details.get("definition"):
                default = DEFAULT_ROLE_DEFINITIONS.get(role, {})
                details["definition"] = default.get("definition", f"Agent that performs {role} functions.")

        return agent_to_role, role_details

    def _gather_agent_samples(self, agents: Set[str], traces: List[Dict[str, Any]]) -> Dict[str, str]:
        samples: Dict[str, str] = {}
        for agent in agents:
            agent_lower = agent.lower()
            snippets: List[str] = []
            for trace in traces[:50]:
                trajectory = get_trajectory_text(trace)
                if not trajectory:
                    continue
                idx = trajectory.lower().find(agent_lower)
                if idx < 0:
                    continue
                start = max(0, idx - 100)
                end = min(len(trajectory), idx + 400)
                snippets.append(trajectory[start:end])
                if len(snippets) >= 3:
                    break
            samples[agent] = "\n---\n".join(snippets) if snippets else "(no trace samples found)"
        return samples

    # ───── Capability detection ─────

    def _detect_capabilities(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        capabilities: Dict[str, Any] = {
            "has_tool_calling": False,
            "has_code_execution": False,
            "has_web_browsing": False,
            "tool_names_seen": [],
            "interaction_style": "direct_reasoning",
        }

        tool_names: Set[str] = set()
        sample = traces[:min(100, len(traces))]
        tool_call_count = 0

        for trace in sample:
            text = trace.get("raw_trajectory", "")
            if not text:
                continue

            for pattern in _TOOL_CALL_PATTERNS:
                if pattern.search(text):
                    tool_call_count += 1
                    capabilities["has_tool_calling"] = True
                    break

            for pattern in _CODE_EXEC_PATTERNS:
                if pattern.search(text):
                    capabilities["has_code_execution"] = True
                    break

            for pattern in _WEB_PATTERNS:
                if pattern.search(text):
                    capabilities["has_web_browsing"] = True
                    break

            for pattern in _TOOL_NAME_PATTERNS:
                for match in pattern.findall(text):
                    if 2 < len(match) < 50 and match.lower() not in ("unknown", "none", "null"):
                        tool_names.add(match)

        capabilities["tool_names_seen"] = sorted(tool_names)[:30]

        if capabilities["has_tool_calling"] and tool_call_count > len(sample) * 0.3:
            capabilities["interaction_style"] = "tool_calling"
        elif capabilities["has_code_execution"]:
            capabilities["interaction_style"] = "code_execution"
        elif capabilities["has_tool_calling"]:
            capabilities["interaction_style"] = "mixed"

        return capabilities

    # ───── Fallbacks (used when the LLM returns blanks) ─────

    def _fallback_architecture(
        self, role_details: Dict[str, Dict[str, Any]], capabilities: Dict[str, Any]
    ) -> Dict[str, Any]:
        active_roles = [r for r, d in role_details.items() if d.get("agents")]

        orchestrating: List[str] = []
        validating: List[str] = []
        producing: List[str] = []
        for role in active_roles:
            text = " ".join([
                role_details.get(role, {}).get("purpose", "").lower(),
                role_details.get(role, {}).get("definition", "").lower(),
                role,
            ])
            if any(w in text for w in ["orchestrat", "coordinat", "route", "select", "manage", "direct"]):
                orchestrating.append(role)
            elif any(w in text for w in ["verif", "validat", "check", "review", "test", "evaluat"]):
                validating.append(role)
            else:
                producing.append(role)

        if len(active_roles) <= 1:
            topology = "single-agent"
        elif orchestrating:
            topology = "hierarchical"
        else:
            topology = "sequential"

        verification = "dedicated-checker" if validating else "none"

        handoffs = []
        if producing and validating:
            handoffs.append({
                "from_agent": producing[0],
                "to_agent": validating[0],
                "what_is_passed": "output for validation",
                "failure_risk": "Output context lost or misinterpreted",
            })
        if orchestrating and producing:
            handoffs.append({
                "from_agent": orchestrating[0],
                "to_agent": producing[0],
                "what_is_passed": "task routing/selection",
                "failure_risk": "Wrong routing or selection decision",
            })

        if orchestrating:
            termination = f"{orchestrating[0]} (orchestrator)"
        elif validating:
            termination = f"{validating[0]} (accept/reject terminates)"
        elif producing:
            termination = f"{producing[0]} (single pass)"
        else:
            termination = "unknown"

        return {
            "topology": topology,
            "topology_details": f"Inferred from {len(active_roles)} active roles: {', '.join(active_roles)}",
            "verification_pattern": verification,
            "verification_details": (
                "Dedicated validation agents verify outputs"
                if verification != "none" else "No dedicated verification agent found"
            ),
            "termination_owner": termination,
            "critical_handoffs": handoffs,
        }

    def _fallback_trace_format(self, sample_traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        markers: Set[str] = set()
        for trace in sample_traces:
            text = trace.get("raw_trajectory", "")
            for m in re.findall(r"===\s*(\w+)\s*===", text):
                markers.add(f"=== {m} ===")
            for m in re.findall(r"\[(\w+)\]", text[:500]):
                if m.upper() in ("SYSTEM", "USER", "ASSISTANT", "TOOL"):
                    markers.add(f"[{m}]")

        return {
            "agent_markers": list(markers)[:10],
            "key_fields": [],
            "output_structure": "Raw text trajectory with agent markers",
            "example_patterns": list(markers)[:5],
        }


def _normalize_agents(agents: Set[str]) -> Set[str]:
    """Deduplicate case variants ('Solver' / 'SOLVER' / 'solver') sensibly.

    Multi-word agent names (e.g. 'Chief Technology Officer') keep their
    original casing; single-word names become lowercase canonical.
    """
    normalized: Dict[str, str] = {}
    for agent in agents:
        key = agent.lower().strip()
        if key not in normalized or agent[0].isupper():
            normalized[key] = agent
    result: Set[str] = set()
    for key, original in normalized.items():
        if " " in original or "_" in original:
            result.add(original)
        else:
            result.add(key)
    return result
