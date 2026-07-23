"""Step 8: TaxonomyChecker.

Final pass over the taxonomy. For each category, runs per-code structural
and quality checks, then looks for *coverage gaps* (failure types or roles
or subdomains with no codes) and *overlaps* (semantically duplicate codes
in the same category). Anything found is then fixed via the LLM.

This is where the cleanest, most readable output is shaped — bad heuristics
get rewritten, missing coverage gets filled in, redundant codes are merged.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from adamast.learning.vendor.llm import LLMClient, extract_json
from adamast.learning.vendor.pipeline.prompts import (
    A_FAILURE_CATEGORY_KEYWORDS,
    B_CODE_A_TYPE_KEYWORDS,
    PLACEHOLDER_PATTERNS,
    render_prompt_asset,
)
from adamast.learning.vendor.utils import normalize_code_ids, progress, truncate_text


class TaxonomyChecker:
    """Final validation + fixing of the generated taxonomy."""

    def __init__(self, client: LLMClient, structure_info: Dict[str, Any], domain_info: Dict[str, Any]):
        self.client = client
        self.structure_info = structure_info
        self.domain_info = domain_info
        self._placeholder_re = [re.compile(p, re.IGNORECASE) for p in PLACEHOLDER_PATTERNS]
        self.role_types = list(
            (structure_info.get("discovered_agents", {}) or {}).get("role_details", {}).keys()
        )

    def check_and_fix(
        self,
        a_codes: List[Dict[str, Any]],
        b_codes: List[Dict[str, Any]],
        c_codes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        progress("\nStep 8: Taxonomy Checker")

        issues = {"a": [], "b": [], "c": []}

        progress("  Checking A codes...")
        for code in a_codes:
            iss = self._check_a_code(code)
            if iss:
                issues["a"].append({"code": code.get("code"), "issues": iss})

        a_coverage_gaps = self._check_a_coverage(a_codes)
        if a_coverage_gaps:
            progress(f"  A-code coverage gaps: {a_coverage_gaps}")

        a_overlaps = self._check_overlaps(a_codes, "A")
        if a_overlaps:
            progress(f"  A-code overlaps detected: {len(a_overlaps)}")

        progress("  Checking B codes...")
        for code in b_codes:
            iss = self._check_b_code(code)
            if iss:
                issues["b"].append({"code": code.get("code"), "issues": iss})

        b_coverage_gaps = self._check_b_coverage(b_codes)
        if b_coverage_gaps:
            progress(f"  B-code coverage gaps (missing roles): {b_coverage_gaps}")

        b_overlaps = self._check_overlaps(b_codes, "B")
        if b_overlaps:
            progress(f"  B-code overlaps detected: {len(b_overlaps)}")

        progress("  Checking C codes...")
        for code in c_codes:
            iss = self._check_c_code(code)
            if iss:
                issues["c"].append({"code": code.get("code"), "issues": iss})

        c_coverage_gaps = self._check_c_coverage(c_codes)
        if c_coverage_gaps:
            progress(f"  C-code coverage gaps (missing subdomains): {c_coverage_gaps}")

        c_overlaps = self._check_overlaps(c_codes, "C") if len(c_codes) >= 2 else []
        if c_overlaps:
            progress(f"  C-code overlaps detected: {len(c_overlaps)}")

        total_issues = sum(len(issues[k]) for k in issues)
        progress(f"  Found {total_issues} codes with issues")

        if total_issues > 0:
            progress("  Fixing issues...")
            a_codes = self._fix_codes(a_codes, issues["a"], "A")
            b_codes = self._fix_codes(b_codes, issues["b"], "B")
            c_codes = self._fix_codes(c_codes, issues["c"], "C")

        if a_coverage_gaps or a_overlaps:
            progress("  Fixing A-code coverage gaps and overlaps...")
            a_codes = self._fix_a_coverage_and_overlaps(a_codes, a_coverage_gaps, a_overlaps)

        if b_coverage_gaps or b_overlaps:
            progress("  Fixing B-code coverage gaps and overlaps...")
            b_codes = self._fix_b_coverage_and_overlaps(b_codes, b_coverage_gaps, b_overlaps)

        if c_coverage_gaps or c_overlaps:
            progress("  Fixing C-code coverage gaps and overlaps...")
            c_codes = self._fix_c_coverage_and_overlaps(c_codes, c_coverage_gaps, c_overlaps)

        return {
            "category_a": a_codes,
            "category_b": b_codes,
            "category_c": c_codes,
            "issues_found": issues,
            "a_coverage_gaps": a_coverage_gaps,
            "a_overlaps_detected": a_overlaps,
            "b_coverage_gaps": b_coverage_gaps,
            "b_overlaps_detected": b_overlaps,
            "c_coverage_gaps": c_coverage_gaps,
            "c_overlaps_detected": c_overlaps,
            "total_issues": total_issues,
        }

    # ───── Per-code structural checks ─────

    def _check_a_code(self, code: Dict[str, Any]) -> List[str]:
        issues: List[str] = []
        name = code.get("name", "").lower()

        for role_type in self.role_types:
            if role_type in name:
                issues.append(f"Name contains role type '{role_type}' (not allowed for A codes)")
                break

        definition = code.get("definition", "")
        if not definition or len(definition) < 10:
            issues.append("Missing or too short definition")

        issues.extend(self._heuristic_quality(code.get("detection_heuristics", [])))

        if not code.get("when_to_use"):
            issues.append("Missing when_to_use")
        if not code.get("when_not_to_use"):
            issues.append("Missing when_not_to_use")

        evidence = code.get("evidence", "")
        if evidence and evidence not in ("theoretical", "observed"):
            issues.append(f"Invalid evidence value '{evidence}' — must be 'theoretical' or 'observed'")

        return issues

    def _check_b_code(self, code: Dict[str, Any]) -> List[str]:
        issues: List[str] = []
        name = code.get("name", "").lower()
        definition = code.get("definition", "").lower()
        when_to_use = code.get("when_to_use", "").lower()

        if not any(role in name for role in self.role_types):
            role_str = ", ".join(r.capitalize() for r in self.role_types)
            issues.append(f"Name must contain role type ({role_str})")

        if not code.get("applies_to_role"):
            issues.append("Missing applies_to_role field")

        if not definition or len(definition) < 10:
            issues.append("Missing or too short definition")

        issues.extend(self._heuristic_quality(code.get("detection_heuristics", [])))

        if not code.get("when_to_use"):
            issues.append("Missing when_to_use")
        if not code.get("when_not_to_use"):
            issues.append("Missing when_not_to_use")

        combined = f"{name} {definition} {when_to_use}"
        for keyword in B_CODE_A_TYPE_KEYWORDS:
            if keyword in combined:
                issues.append(
                    f"B code appears to describe a system/output failure "
                    f"('{keyword}') — this belongs in Category A, not B. "
                    f"B codes are only about quality of work done, not about "
                    f"whether output was produced."
                )
                break

        return issues

    def _check_c_code(self, code: Dict[str, Any]) -> List[str]:
        issues: List[str] = []
        name = code.get("name", "").lower()
        for role_type in self.role_types:
            if role_type in name:
                issues.append(f"Name contains role type '{role_type}' (not allowed for C codes)")
                break

        definition = code.get("definition", "")
        if not definition or len(definition) < 10:
            issues.append("Missing or too short definition")

        if not code.get("detection_heuristics"):
            issues.append("Missing detection heuristics")

        if not code.get("when_to_use"):
            issues.append("Missing when_to_use")
        if not code.get("when_not_to_use"):
            issues.append("Missing when_not_to_use")

        return issues

    def _heuristic_quality(self, heuristics: List[Any]) -> List[str]:
        issues: List[str] = []
        if not heuristics:
            return ["Missing detection heuristics"]

        placeholder_count = 0
        for h in heuristics:
            if not isinstance(h, str):
                continue
            for pattern in self._placeholder_re:
                if pattern.search(h):
                    placeholder_count += 1
                    break
        if placeholder_count > 0:
            issues.append(
                f"Has {placeholder_count} placeholder/templated heuristic(s) — "
                f"heuristics must be grounded in observable signals"
            )

        short_count = sum(1 for h in heuristics if isinstance(h, str) and len(h) < 15)
        if short_count == len(heuristics) and heuristics:
            issues.append("All heuristics are very short — need more specific detection signals")

        return issues

    # ───── Coverage checks ─────

    def _check_a_coverage(self, a_codes: List[Dict[str, Any]]) -> List[str]:
        all_text = " ".join(
            " ".join([
                c.get("name", ""),
                c.get("definition", ""),
                c.get("when_to_use", ""),
            ]).lower()
            for c in a_codes
        )

        caps = self.structure_info.get("capabilities", {}) or {}
        interaction = caps.get("interaction_style", "direct_reasoning")
        agents = self.structure_info.get("discovered_agents", {}).get("agents", []) or []
        skip = set()
        if interaction == "direct_reasoning" and not caps.get("has_tool_calling", False):
            skip.add("tool_api")
        if len(agents) <= 1:
            skip.add("communication")

        gaps: List[str] = []
        for cat, keywords in A_FAILURE_CATEGORY_KEYWORDS.items():
            if cat in skip:
                continue
            if not any(kw in all_text for kw in keywords):
                gaps.append(cat)
        return gaps

    def _check_b_coverage(self, b_codes: List[Dict[str, Any]]) -> List[str]:
        role_details = self.structure_info.get("discovered_agents", {}).get("role_details", {}) or {}
        active_roles = [r for r, d in role_details.items() if d.get("agents")]
        covered = {c.get("applies_to_role") for c in b_codes if c.get("applies_to_role")}
        return [r for r in active_roles if r not in covered]

    def _check_c_coverage(self, c_codes: List[Dict[str, Any]]) -> List[str]:
        subdomains = self.domain_info.get("subdomains", []) or []
        if not subdomains:
            return []

        all_text = " ".join(
            " ".join([
                c.get("name", ""),
                c.get("definition", ""),
                c.get("when_to_use", ""),
                *[str(h) for h in c.get("detection_heuristics", []) if isinstance(h, str)],
            ]).lower()
            for c in c_codes
        )

        gaps: List[str] = []
        for subdomain in subdomains:
            sd_lower = subdomain.lower()
            if sd_lower in all_text:
                continue
            if any(len(w) > 3 and w in all_text for w in sd_lower.split()):
                continue
            gaps.append(subdomain)
        return gaps

    # ───── Overlap detection ─────

    def _check_overlaps(self, codes: List[Dict[str, Any]], category: str) -> List[Dict[str, Any]]:
        if len(codes) < 2:
            return []

        def summarize(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return [{
                "code": c.get("code", ""),
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
                **({"applies_to_role": c.get("applies_to_role", "")} if category == "B" else {}),
            } for c in codes]

        category_context = {
            "A": "Category A (System Failure) codes",
            "B": "Category B (role-specific quality failure) codes — note: codes for DIFFERENT roles cannot overlap",
            "C": "Category C (Domain Reasoning Failure) codes — the test: can you describe a concrete scenario where code X applies but code Y does NOT? If you cannot, they overlap",
        }[category]

        prompt = render_prompt_asset(
            "check_overlaps.md",
            category_context=category_context,
            codes=summarize(codes),
            category=category,
        )

        try:
            return extract_json(self.client.chat(prompt)).get("overlaps", [])
        except Exception as e:  # noqa: BLE001
            progress(f"  [!] {category}-overlap check error: {e}")
            return []

    # ───── Generic per-code fixer ─────

    def _fix_codes(
        self,
        codes: List[Dict[str, Any]],
        issues_list: List[Dict[str, Any]],
        category: str,
    ) -> List[Dict[str, Any]]:
        if not issues_list:
            return codes

        trace_format = self.structure_info.get("trace_format", {}) or {}
        key_fields = trace_format.get("key_fields", [])
        fields_str = (
            ", ".join(f.get("field_name", f.get("field", "")) for f in key_fields[:5])
            if key_fields else "agent output, verdict, final answer"
        )

        codes_to_fix = []
        for entry in issues_list:
            code_id = entry.get("code")
            for c in codes:
                if c.get("code") == code_id:
                    codes_to_fix.append({"code": c, "issues": entry.get("issues", [])})
                    break

        if not codes_to_fix:
            return codes

        role_str = ", ".join(r.capitalize() for r in self.role_types)
        role_prefix = ", ".join(r.capitalize() + "_" for r in self.role_types)

        if category in ("A", "C"):
            naming_rule = f"- Names must NOT contain role types ({role_str})"
        else:
            naming_rule = f"- Names MUST contain role type ({role_prefix})"
        applies_rule = "- Must have applies_to_role field" if category == "B" else ""

        prompt = render_prompt_asset(
            "fix_validation_issues.md",
            category=category,
            naming_rule=naming_rule,
            applies_rule=applies_rule,
            fields_str=fields_str,
            codes_to_fix=codes_to_fix,
        )

        try:
            result = extract_json(self.client.chat(prompt))
            fixed = result.get("fixed_codes", [])

            fixed_by_id: Dict[str, Dict[str, Any]] = {}
            for entry in fixed:
                if isinstance(entry.get("code"), dict):
                    inner = entry["code"]
                    actual = dict(inner)
                    for key, val in entry.items():
                        if key not in ("code", "issues") and key not in actual:
                            actual[key] = val
                else:
                    actual = dict(entry)
                    actual.pop("issues", None)

                cid = actual.get("code")
                if isinstance(cid, dict):
                    cid = cid.get("code", str(cid))
                if isinstance(cid, str):
                    fixed_by_id[cid] = actual

            updated = [fixed_by_id.get(c.get("code"), c) for c in codes]
            progress(f"    Fixed {len(fixed)} {category} codes")
            return updated
        except Exception as e:  # noqa: BLE001
            progress(f"    [!] Fix error: {e}")
            return codes

    # ───── Coverage + overlap fixers ─────

    def _fix_a_coverage_and_overlaps(
        self,
        a_codes: List[Dict[str, Any]],
        coverage_gaps: List[str],
        overlaps: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        arch = self.structure_info.get("architecture", {}) or {}
        role_details = self.structure_info.get("discovered_agents", {}).get("role_details", {}) or {}

        arch_summary = f"Topology: {arch.get('topology', 'Unknown')}"
        handoffs = arch.get("critical_handoffs", [])
        if handoffs:
            arch_summary += "\nHandoffs: " + ", ".join(
                f"{h.get('from_agent','?')}->{h.get('to_agent','?')}" for h in handoffs[:5]
            )

        roles_summary = ", ".join(
            f"{r}: {len(d.get('agents', []))} agents"
            for r, d in role_details.items() if d.get("agents")
        )

        category_descriptions = {
            "output_issues": "Output issues (empty, truncated, malformed, unusable output)",
            "context_memory": "Context/memory issues (overflow, context loss, forgetting prior info)",
            "communication": "Inter-agent communication (handoff failures, information lost between agents)",
            "behavioral": "Behavioral anomalies (looping, repetition, refusal, degradation)",
            "execution": "Execution errors (timeouts, crashes, API errors, resource exhaustion)",
            "instruction": "Instruction compliance (ignoring system prompt, wrong problem, format violations)",
            "tool_api": "Tool/API interaction (wrong tool called, wrong arguments, tool errors, misinterpreted responses)",
        }

        gap_section = ""
        if coverage_gaps:
            gaps_text = "\n".join(f"  - {category_descriptions.get(g, g)}" for g in coverage_gaps)
            gap_section = (
                "\nCOVERAGE GAPS — the following failure categories have NO codes:\n"
                f"{gaps_text}\n\nGenerate NEW codes to fill these gaps. Each gap needs at least one code."
            )

        overlap_section = ""
        if overlaps:
            overlap_section = (
                "\nOVERLAPPING CODES — merge these:\n"
                f"{json.dumps(overlaps, indent=2)}\n\n"
                "For each overlap, merge the two codes into one stronger code. Keep the better "
                "name and combine the detection heuristics."
            )

        role_str = ", ".join(r.capitalize() for r in role_details.keys())
        prompt = render_prompt_asset(
            "fix_category_a.md",
            a_codes=self._summarize_basic(a_codes),
            arch_summary=arch_summary,
            roles_summary=roles_summary,
            gap_section=gap_section,
            overlap_section=overlap_section,
            role_str=role_str,
        )

        try:
            result = extract_json(self.client.chat(prompt))
            new_codes = [c for c in result.get("codes", []) if isinstance(c, dict)]
            if not new_codes:
                return a_codes
            progress(f"    Changes: {result.get('changes_made', [])[:5]}")
            return normalize_code_ids(new_codes, "A")
        except Exception as e:  # noqa: BLE001
            progress(f"  [!] Coverage/overlap fix error: {e}")
            return a_codes

    def _fix_b_coverage_and_overlaps(
        self,
        b_codes: List[Dict[str, Any]],
        coverage_gaps: List[str],
        overlaps: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        role_details = self.structure_info.get("discovered_agents", {}).get("role_details", {}) or {}

        gap_section = ""
        if coverage_gaps:
            parts = []
            for role in coverage_gaps:
                details = role_details.get(role, {})
                agents = details.get("agents", [])
                definition = details.get("definition", "N/A")
                parts.append(
                    f"  - {role}: {definition} "
                    f"(agents: {', '.join(agents[:5]) if agents else 'unknown'})"
                )
            gap_section = (
                "\nMISSING ROLE COVERAGE — these roles have agents but NO B codes:\n"
                + "\n".join(parts)
                + "\n\nGenerate B codes for each missing role. Each role needs at least 2 codes covering "
                "distinct quality failures relevant to that role's purpose.\n\n"
                "CRITICAL: B codes are about QUALITY of work done, NOT about system failures.\n"
                "- GOOD: Solver_Wrong_Approach, Checker_Superficial_Verification\n"
                "- BAD: Solver_No_Output (this is a system failure -> Category A)"
            )

        overlap_section = ""
        if overlaps:
            overlap_section = (
                "\nOVERLAPPING CODES — merge these:\n"
                f"{json.dumps(overlaps, indent=2)}\n\n"
                "For each overlap, merge the two codes into one stronger code."
            )

        prompt = render_prompt_asset(
            "fix_category_b.md",
            b_codes=[{
                "code": c.get("code", ""),
                "name": c.get("name", "")[:60],
                "definition": truncate_text(c.get("definition", ""), 150),
                "applies_to_role": c.get("applies_to_role", ""),
            } for c in b_codes],
            gap_section=gap_section,
            overlap_section=overlap_section,
            role_prefixes=", ".join(r.capitalize() + "_" for r in role_details.keys()),
        )

        try:
            result = extract_json(self.client.chat(prompt))
            new_codes = [c for c in result.get("codes", []) if isinstance(c, dict)]
            if not new_codes:
                return b_codes
            progress(f"    B-code changes: {result.get('changes_made', [])[:5]}")
            return normalize_code_ids(new_codes, "B")
        except Exception as e:  # noqa: BLE001
            progress(f"  [!] B coverage/overlap fix error: {e}")
            return b_codes

    def _fix_c_coverage_and_overlaps(
        self,
        c_codes: List[Dict[str, Any]],
        coverage_gaps: List[str],
        overlaps: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        domain_name = self.domain_info.get("domain", {}).get("name", "Unknown")

        gaps_str = ""
        if coverage_gaps:
            gaps_str = (
                "\nCOVERAGE GAPS — these subdomains have NO C codes covering their characteristic reasoning failures:\n"
                f"{json.dumps(coverage_gaps, indent=2)}\n\n"
                "For each gap, generate ONE new C code that captures the most common/important reasoning\n"
                "failure type for that subdomain. The code must:\n"
                "- Be detectable from the trace alone (no need to solve the problem)\n"
                "- Not overlap with existing codes\n"
                "- Follow all C-code naming rules (no role names)"
            )

        overlaps_str = ""
        if overlaps:
            overlaps_str = f"\nOVERLAPS DETECTED — merge these:\n{json.dumps(overlaps, indent=2)}\n"

        prompt = render_prompt_asset(
            "fix_category_c.md",
            domain_name=domain_name,
            c_codes=self._summarize_basic(c_codes),
            gaps_str=gaps_str,
            overlaps_str=overlaps_str,
        )

        try:
            result = extract_json(self.client.chat(prompt))
            new_codes = result.get("codes", [])
            if not new_codes:
                return c_codes

            existing_by_name = {c.get("name", "").lower(): c for c in c_codes}
            final: List[Dict[str, Any]] = []
            for nc in new_codes:
                if not isinstance(nc, dict):
                    continue
                nc_name = nc.get("name", "").lower()
                if nc_name in existing_by_name:
                    final.append(existing_by_name[nc_name])
                else:
                    final.append(nc)

            return normalize_code_ids(final, "C")
        except Exception as e:  # noqa: BLE001
            progress(f"  [!] C coverage/overlap fix error: {e}")
            return c_codes

    def _summarize_basic(self, codes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{
            "code": c.get("code", ""),
            "name": c.get("name", "")[:60],
            "definition": truncate_text(c.get("definition", ""), 150),
        } for c in codes]
