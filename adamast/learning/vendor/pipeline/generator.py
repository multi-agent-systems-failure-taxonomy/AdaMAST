"""Category A/B/C taxonomy generation orchestration.

Model-facing generation instructions live in ``vendor/adamast/pipeline/assets``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from adamast.learning.vendor.config import PipelineConfig
from adamast.learning.vendor.llm import LLMClient, extract_json
from adamast.learning.vendor.pipeline.prompts import (
    A_FAILURE_CATEGORIES,
    build_b_role_guidance,
    render_prompt_asset,
)
from adamast.learning.vendor.traces.signals import SignalExtractor
from adamast.learning.vendor.utils import (
    format_trace_for_prompt,
    normalize_code_ids,
    progress,
    stratified_sample,
    truncate_text,
)


class CategoryGenerator:
    """Generate codes for one taxonomy category (A, B, or C) via two parallel stages."""

    def __init__(
        self,
        client: LLMClient,
        config: PipelineConfig,
        category: str,
        domain_info: Dict[str, Any],
        structure_info: Dict[str, Any],
        trace_signals: Optional[Dict[str, Any]] = None,
    ):
        assert category in ("A", "B", "C"), f"Bad category: {category}"
        self.client = client
        self.config = config
        self.category = category
        self.domain_info = domain_info or {}
        self.structure_info = structure_info or {}
        self.trace_signals = trace_signals or {}

    def generate(
        self,
        traces: List[Dict[str, Any]],
        existing_codes: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        progress(f"\nStep {3 + ['A', 'B', 'C'].index(self.category)}: Category {self.category} Generator")
        progress("  No base codes — fully generated from analysis")

        if self.category == "B":
            active_roles = self._active_roles()
            progress(f"  Active roles with agents: {active_roles}")
            if not active_roles:
                progress("  WARNING: No agents discovered for any role - B codes will be empty")
                return []

        if self.category == "A":
            stage1_name, stage2_name = "Architectural", "Empirical"
        elif self.category == "C":
            stage1_name, stage2_name = "Domain-Seeded", "Trace-Grounded"
        else:
            stage1_name, stage2_name = "Theoretical", "Empirical"

        # A-Architectural runs without traces; everyone else gets a slice.
        mid = len(traces) // 2
        traces_stage1 = stratified_sample(traces[:mid] if mid > 0 else traces, self.config.traces_per_agent)
        traces_stage2 = stratified_sample(traces[mid:] if mid > 0 else traces, self.config.traces_per_agent)

        with ThreadPoolExecutor(max_workers=2) as executor:
            future1 = executor.submit(
                self._run_stage,
                [] if self.category == "A" else traces_stage1,
                stage1_name, existing_codes,
            )
            future2 = executor.submit(
                self._run_stage, traces_stage2, stage2_name, existing_codes,
            )
            codes_stage1 = future1.result()
            codes_stage2 = future2.result()

        progress(f"  {stage1_name} stage: {len(codes_stage1)} codes")
        progress(f"  {stage2_name} stage: {len(codes_stage2)} codes")

        merged = self._merge_codes(codes_stage1, codes_stage2)
        progress(f"  Merged total: {len(merged)} codes")

        if self.category == "A":
            merged = self._sanitize_a_codes(merged)

        return merged

    # ───── A-code sanitization ─────

    def _sanitize_a_codes(self, codes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Drop A codes whose names imply they're really role-specific (-> B).

        The "swap test": if replacing the agent with one of a different role
        would make the code inapplicable, the code belongs in B. We approximate
        the swap test by checking whether role-related vocabulary appears in
        the code's *name* (a much stronger signal than appearing in the body).
        """
        role_details = self.structure_info.get("discovered_agents", {}).get("role_details", {})
        indicators: Dict[str, List[str]] = {}
        for role, details in role_details.items():
            agent_names = [a.lower() for a in details.get("agents", []) if len(a) > 2]
            purpose_words = [w for w in details.get("purpose", "").lower().split() if len(w) > 4]
            indicators[role] = [role, *agent_names, *purpose_words]

        sanitized: List[Dict[str, Any]] = []
        removed: List[tuple] = []
        for code in codes:
            name_lower = code.get("name", "").lower()
            is_role_specific = False
            for role, role_indicators in indicators.items():
                if any(ind in name_lower for ind in role_indicators):
                    is_role_specific = True
                    removed.append((code.get("code", ""), code.get("name", ""), role))
                    break
            if not is_role_specific:
                sanitized.append(code)

        if removed:
            progress(f"  A-code sanitization: removed {len(removed)} role-specific codes:")
            for code_id, name, role in removed:
                progress(f"    {code_id} '{name}' -> role-specific to {role}")

        return sanitized

    # ───── Context builders ─────

    def _active_roles(self) -> List[str]:
        role_details = self.structure_info.get("discovered_agents", {}).get("role_details", {})
        return [role for role, details in role_details.items() if details.get("agents")]

    def _trace_format_context(self) -> str:
        trace_format = self.structure_info.get("trace_format", {}) or {}
        lines = ["\n=== TRACE FORMAT CONTEXT ==="]
        markers = trace_format.get("agent_markers", [])
        if markers:
            lines.append(f"Agent markers in traces: {markers}")
        key_fields = trace_format.get("key_fields", [])
        if key_fields:
            lines.append("\nDiscovered fields:")
            for field in key_fields:
                name = field.get("field_name", field.get("field", "?"))
                desc = field.get("description", "")
                lines.append(f"  * {name}: {desc}")
        else:
            lines.append("\nUse general trace content patterns for heuristics.")
        return "\n".join(lines)

    def _architecture_context(self) -> str:
        arch = self.structure_info.get("architecture", {}) or {}
        agents = self.structure_info.get("discovered_agents", {}) or {}
        lines = ["\n=== SYSTEM ARCHITECTURE ==="]
        lines.append(f"Topology: {arch.get('topology', 'Unknown')}")
        if arch.get("topology_details"):
            lines.append(f"Details: {arch['topology_details']}")
        lines.append(f"\nVerification: {arch.get('verification_pattern', 'Unknown')}")
        if arch.get("verification_details"):
            lines.append(f"Details: {arch['verification_details']}")
        lines.append(f"\nTermination owner: {arch.get('termination_owner', 'Unknown')}")

        handoffs = arch.get("critical_handoffs", [])
        if handoffs:
            lines.append("\nCritical handoffs:")
            for h in handoffs:
                lines.append(
                    f"  {h.get('from_agent','?')} -> {h.get('to_agent','?')}: "
                    f"passes {h.get('what_is_passed','?')} "
                    f"(risk: {h.get('failure_risk','?')})"
                )

        role_details = agents.get("role_details", {})
        if role_details:
            lines.append("\n=== AGENTS & ROLES ===")
            for role, details in role_details.items():
                agent_list = details.get("agents", [])
                if not agent_list:
                    continue
                purpose = details.get("purpose", "")
                shown = agent_list[:5]
                more = f" (+{len(agent_list)-5} more)" if len(agent_list) > 5 else ""
                purpose_str = f" ({purpose})" if purpose else ""
                lines.append(f"{role.upper()}{purpose_str}: {', '.join(shown)}{more}")

        return "\n".join(lines)

    def _agent_context(self) -> str:
        agents = self.structure_info.get("discovered_agents", {})
        if not agents:
            return ""
        lines = ["\n=== DISCOVERED AGENTS ==="]
        for role, details in agents.get("role_details", {}).items():
            agent_list = details.get("agents", [])
            if not agent_list:
                continue
            purpose = details.get("purpose", "")
            shown = agent_list[:5]
            more = f" (+{len(agent_list)-5} more)" if len(agent_list) > 5 else ""
            purpose_str = f" ({purpose})" if purpose else ""
            lines.append(f"{role.upper()}{purpose_str}: {', '.join(shown)}{more}")
        return "\n".join(lines)

    def _domain_context(self) -> str:
        if not self.domain_info:
            return ""
        lines = ["\n=== DOMAIN KNOWLEDGE ==="]
        lines.append(f"Domain: {self.domain_info.get('domain', {}).get('name', 'Unknown')}")
        if self.domain_info.get("subdomains"):
            lines.append(f"Subdomains: {', '.join(self.domain_info['subdomains'][:5])}")
        patterns = self.domain_info.get("common_error_patterns", [])
        if patterns:
            lines.append("Common error patterns:")
            for p in patterns[:5]:
                lines.append(f"  - {p.get('name', '')}: {p.get('description', '')}")
        return "\n".join(lines)

    def _signal_context(self) -> str:
        if not self.trace_signals:
            return ""
        return SignalExtractor(verbose=False).format_for_prompt(self.trace_signals)

    def _lightweight_domain_context(self) -> str:
        if not self.domain_info:
            return ""
        domain = self.domain_info.get("domain", {})
        return (
            "\n=== DOMAIN CONTEXT ===\n"
            f"Domain: {domain.get('name', 'Unknown')}\n"
            f"Content type: {domain.get('content_type', 'Unknown')}\n"
            f"Task complexity: {domain.get('task_complexity', 'Unknown')}"
        )

    def _capabilities_context(self) -> str:
        caps = self.structure_info.get("capabilities", {}) or {}
        if not caps:
            return ""
        lines = ["\n=== AGENT CAPABILITIES ==="]
        style = caps.get("interaction_style", "direct_reasoning")
        lines.append(f"Primary interaction style: {style}")

        if style == "tool_calling":
            lines.extend([
                "Agents primarily interact with external tools/APIs to accomplish tasks.",
                "B codes should focus on quality of TOOL USAGE and DECISION-MAKING:",
                "  - Did the agent select the right tool for the situation?",
                "  - Did it pass correct arguments?",
                "  - Did it correctly interpret tool responses?",
                "  - Did it follow required procedures (e.g., confirmation before action)?",
                "  - Did it chain tool calls in the right sequence?",
            ])
        elif style == "code_execution":
            lines.extend([
                "Agents write and execute code to accomplish tasks.",
                "B codes should focus on quality of CODE and APPROACH:",
                "  - Is the code correct for the problem?",
                "  - Does it handle edge cases?",
                "  - Is the approach appropriate?",
            ])
        elif style == "mixed":
            lines.append("Agents use a mix of direct reasoning and tool/API calls.")

        tools = caps.get("tool_names_seen", [])
        if tools:
            lines.append(f"\nTools/APIs available: {', '.join(tools[:15])}")
        return "\n".join(lines)

    def _domain_error_seed_context(self) -> str:
        if not self.domain_info:
            return ""
        lines = ["\n=== DOMAIN ERROR PATTERNS (from domain analysis) ==="]

        subdomains = self.domain_info.get("subdomains", [])
        if subdomains:
            lines.append(f"\nSUBDOMAINS in this domain: {', '.join(subdomains)}")
            lines.extend([
                "IMPORTANT: Generate C codes that cover reasoning failures across ALL these",
                "subdomains, not just the most common ones. Each subdomain may have its own",
                "characteristic error types. If a subdomain has distinctive reasoning patterns",
                "(e.g., spatial reasoning, inequality chains, inductive proofs), ensure those",
                "failure modes are represented.",
            ])

        patterns = self.domain_info.get("common_error_patterns", [])
        if patterns:
            lines.append("\nKnown error patterns in this domain:")
            for p in patterns:
                lines.append(f"  - {p.get('name', '')}: {p.get('description', '')}")
                for h in p.get("detection_hints", [])[:2]:
                    lines.append(f"      detection hint: {h}")
            lines.extend([
                "\nThese known patterns are a STARTING POINT, not a complete list.",
                "You must also identify error types NOT listed above that are common",
                "in the subdomains. Consider:",
                "  - Errors specific to each subdomain's characteristic techniques",
                "  - Errors in logical structure (proof direction, quantifier scope, etc.)",
                "  - Errors in algebraic/symbolic manipulation (sign errors, invalid transforms)",
                "  - Errors in applying standard inequalities or estimates",
                "  - Errors in geometric or spatial reasoning if applicable",
                "  - Errors in proof strategy (proving wrong direction, circular reasoning)",
            ])

        terms = self.domain_info.get("domain_terminology", [])
        error_terms = [t for t in terms if t.get("error_associations")]
        if error_terms:
            lines.append("\nDomain concepts with known error-prone usage:")
            for t in error_terms[:10]:
                lines.append(f"  - {t.get('term', '')} ({t.get('meaning', '')})")
                for a in t.get("error_associations", [])[:2]:
                    lines.append(f"      common error: {a}")

        criteria = self.domain_info.get("correctness_criteria", [])
        if criteria:
            lines.append("\nCorrectness criteria (violations = potential C codes):")
            for c in criteria:
                lines.append(f"  - {c.get('criterion', '')}: {c.get('description', '')}")

        return "\n".join(lines)

    # ───── Stage prompts ─────

    def _stage_prompt(self, stage_name: str) -> str:
        active_roles = self._active_roles()
        role_str = ", ".join(r.capitalize() for r in active_roles)

        if self.category == "A":
            arch_ctx = self._architecture_context()
            caps_ctx = self._capabilities_context()
            domain_lite = self._lightweight_domain_context()
            signal_ctx = self._signal_context()

            common_header = render_prompt_asset(
                "category_a_common_header.md",
                role_str=role_str,
            )

            if stage_name == "Architectural":
                return render_prompt_asset(
                    "category_a_architectural.md",
                    common_header=common_header,
                    a_failure_categories=A_FAILURE_CATEGORIES,
                    arch_ctx=arch_ctx,
                    caps_ctx=caps_ctx,
                    domain_lite=domain_lite,
                    signal_ctx=signal_ctx,
                )
            return render_prompt_asset(
                "category_a_empirical.md",
                common_header=common_header,
                a_failure_categories=A_FAILURE_CATEGORIES,
                signal_ctx=signal_ctx,
                arch_ctx=arch_ctx,
                caps_ctx=caps_ctx,
                domain_lite=domain_lite,
            )

        if self.category == "B":
            arch_ctx = self._architecture_context()
            caps_ctx = self._capabilities_context()
            trace_ctx = self._trace_format_context()
            role_details = self.structure_info.get("discovered_agents", {}).get("role_details", {})

            agents_per_role: Dict[str, List[str]] = {}
            for role in active_roles:
                lst = role_details.get(role, {}).get("agents", [])[:5]
                if lst:
                    agents_per_role[role] = lst

            role_defs_text = "\n".join(
                f"- {role}: {role_details.get(role, {}).get('definition', 'N/A')}"
                for role in active_roles
            )
            role_name_prefixes = ", ".join(f"{r.capitalize()}_" for r in active_roles)
            b_guidance = build_b_role_guidance(role_details)

            stage_task = (
                "Analyze the system ARCHITECTURE and identify role-specific quality "
                "failures based on how agents interact and what decisions they make."
                if stage_name == "Theoretical"
                else "Analyze ACTUAL TRACE CONTENT and find role-specific quality failures that occurred."
            )
            return render_prompt_asset(
                "category_b_stage.md",
                role_name_prefixes=role_name_prefixes,
                role_defs_text=role_defs_text,
                active_roles=active_roles,
                agents_per_role=agents_per_role,
                b_guidance=b_guidance,
                stage_name=stage_name,
                stage_task=stage_task,
                caps_ctx=caps_ctx,
                arch_ctx=arch_ctx,
                trace_ctx=trace_ctx,
            )

        # Category C
        domain_error_ctx = self._domain_error_seed_context()
        domain_ctx = self._domain_context()
        common_header = render_prompt_asset(
            "category_c_common_header.md",
            role_str=role_str,
        )

        if stage_name == "Domain-Seeded":
            return render_prompt_asset(
                "category_c_domain_seeded.md",
                common_header=common_header,
                domain_error_ctx=domain_error_ctx,
                domain_ctx=domain_ctx,
            )
        return render_prompt_asset(
            "category_c_trace_grounded.md",
            common_header=common_header,
            domain_error_ctx=domain_error_ctx,
            domain_ctx=domain_ctx,
        )

    # ───── Stage execution ─────

    def _run_stage(
        self,
        traces: List[Dict[str, Any]],
        stage_name: str,
        existing_codes: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        progress(f"  Running {self.category}-{stage_name}...")

        if self.category == "A" and stage_name == "Architectural":
            traces_text = ""
        else:
            traces_text = "\n\n".join(
                format_trace_for_prompt(t, max_length=3000) for t in traces[:15]
            )

        existing_str = ""
        if existing_codes:
            existing_str = "\nEXISTING CODES FROM OTHER CATEGORIES (don't duplicate concepts):\n"
            for cat_key, codes in existing_codes.items():
                if not codes:
                    continue
                items = codes if isinstance(codes, list) else list(codes.values())
                names = [c.get("name", "")[:40] for c in items][:8]
                existing_str += f"  {cat_key}: {', '.join(names)}\n"

        evidence_field = ', "evidence": "theoretical|observed"' if self.category == "A" else ""

        if self.category == "C":
            requirements = render_prompt_asset(
                "category_c_requirements.md",
                active_roles=", ".join(self._active_roles()),
            )
        else:
            requirements = render_prompt_asset("category_ab_requirements.md")

        traces_section = f"\nSAMPLE TRACES:\n{traces_text}" if traces_text else ""

        b_extra = ""
        if self.category == "B":
            roles = "|".join(self._active_roles())
            b_extra = (
                f', "applies_to_role": "{roles}", '
                f'"agent_heuristics": {{"AgentName": ["agent-specific signal"]}}'
            )

        prompt = render_prompt_asset(
            "category_stage_execution.md",
            stage_name=stage_name,
            category=self.category,
            stage_prompt=self._stage_prompt(stage_name),
            existing_str=existing_str,
            traces_section=traces_section,
            requirements=requirements,
            evidence_field=evidence_field,
            b_extra=b_extra,
        )

        try:
            response = self.client.chat(prompt)
            result = extract_json(response)
            if "_root_list" in result:
                codes = result["_root_list"]
            else:
                codes = result.get("codes", [])
            return [c for c in codes if isinstance(c, dict)]
        except Exception as e:  # noqa: BLE001
            progress(f"  [!] {stage_name} error: {e}")
            return []

    # ───── Merge / dedupe ─────

    def _merge_codes(
        self,
        stage1_codes: List[Dict[str, Any]],
        stage2_codes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        stage1_codes = [c for c in stage1_codes if isinstance(c, dict)]
        stage2_codes = [c for c in stage2_codes if isinstance(c, dict)]
        all_codes = stage1_codes + stage2_codes
        if not all_codes:
            return []

        def summarize(codes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return [{
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
                "evidence": c.get("evidence", ""),
            } for c in codes]

        prompt = render_prompt_asset(
            "category_merge.md",
            category=self.category,
            all_codes=summarize(all_codes),
        )

        try:
            result = extract_json(self.client.chat(prompt))
            kept_names = {c.get("name", "").lower() for c in result.get("kept_codes", [])}
            kept = [c for c in all_codes if c.get("name", "").lower() in kept_names]
            if not kept:
                progress("  [!] Dedup removed all codes, falling back")
                kept = all_codes
            return normalize_code_ids(kept, self.category)
        except Exception as e:  # noqa: BLE001
            progress(f"  [!] Merge error: {e}")
            return normalize_code_ids(all_codes, self.category)
