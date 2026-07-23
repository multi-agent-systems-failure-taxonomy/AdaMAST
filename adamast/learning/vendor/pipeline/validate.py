"""Step 7: CrossCategoryValidator.

Validates codes against the category rules (A has no role names, B *must*
have role names, C has no role names) and asks the LLM to fix any
violations — including moving misplaced codes into the correct category.
"""

from __future__ import annotations

from typing import Any, Dict, List

from adamast.learning.vendor.llm import LLMClient, extract_json
from adamast.learning.vendor.pipeline.prompts import render_prompt_asset
from adamast.learning.vendor.utils import progress, truncate_text


class CrossCategoryValidator:
    """Validate codes are in the correct category and fix misplacements."""

    def __init__(self, client: LLMClient, structure_info: Dict[str, Any]):
        self.client = client
        self.structure_info = structure_info

    def validate(
        self,
        a_codes: List[Dict[str, Any]],
        b_codes: List[Dict[str, Any]],
        c_codes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        progress("\nStep 7: Cross-Category Validator")

        a_codes = [c for c in a_codes if isinstance(c, dict)]
        b_codes = [c for c in b_codes if isinstance(c, dict)]
        c_codes = [c for c in c_codes if isinstance(c, dict)]

        agents = self.structure_info.get("discovered_agents", {})
        agent_names = (agents.get("agents", []) or [])[:10]
        role_names = list((agents.get("role_details", {}) or {}).keys())

        def summarize(codes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return [{
                "code": c.get("code", ""),
                "name": str(c.get("name", ""))[:60],
                "definition": truncate_text(str(c.get("definition", "")), 150),
                "applies_to_role": c.get("applies_to_role", ""),
            } for c in codes]

        prompt = render_prompt_asset(
            "cross_category_validate.md",
            agent_names=agent_names,
            role_names=role_names,
            a_codes=summarize(a_codes),
            b_codes=summarize(b_codes),
            c_codes=summarize(c_codes),
        )

        try:
            result = extract_json(self.client.chat(prompt))
            violations = result.get("violations_fixed", [])
            progress(f"  Violations fixed: {len(violations)}")

            all_codes: Dict[str, Dict[str, Any]] = {}
            for c in a_codes + b_codes + c_codes:
                all_codes[c.get("code", "")] = c

            moves: Dict[str, str] = {}
            for v in violations:
                code_id = v.get("code", "")
                if code_id not in all_codes:
                    continue
                if v.get("new_name"):
                    all_codes[code_id]["name"] = v["new_name"]
                if v.get("applies_to_role"):
                    all_codes[code_id]["applies_to_role"] = v["applies_to_role"]
                if v.get("move_to"):
                    moves[code_id] = v["move_to"]

            final_a = [c for c in a_codes if c.get("code", "") not in moves]
            final_b = [c for c in b_codes if c.get("code", "") not in moves]
            final_c = [c for c in c_codes if c.get("code", "") not in moves]

            for code_id, target in moves.items():
                obj = all_codes[code_id]
                if target == "a":
                    final_a.append(obj)
                elif target == "b":
                    final_b.append(obj)
                elif target == "c":
                    final_c.append(obj)

            return {
                "category_a": final_a,
                "category_b": final_b,
                "category_c": final_c,
                "violations_fixed": violations,
            }
        except Exception as e:  # noqa: BLE001
            progress(f"  [!] Validation error: {e}")
            return {
                "category_a": a_codes,
                "category_b": b_codes,
                "category_c": c_codes,
                "violations_fixed": [],
            }
