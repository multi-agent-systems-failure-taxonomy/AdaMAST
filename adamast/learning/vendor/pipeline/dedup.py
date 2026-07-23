"""Step 6: CrossCategoryDeduplicator.

After A, B, C are generated independently, this stage looks across the
three categories for true semantic duplicates. The boundary rules here
matter: a B code and an A code that describe the same *event* from
different levels of analysis are NOT duplicates — only codes that are
truly synonymous get merged.
"""

from __future__ import annotations

from typing import Any, Dict, List

from adamast.learning.vendor.llm import LLMClient, extract_json
from adamast.learning.vendor.pipeline.prompts import render_prompt_asset
from adamast.learning.vendor.utils import progress, truncate_text


class CrossCategoryDeduplicator:
    """Find and remove duplicate concepts that ended up in two categories."""

    def __init__(self, client: LLMClient):
        self.client = client

    def deduplicate(
        self,
        a_codes: List[Dict[str, Any]],
        b_codes: List[Dict[str, Any]],
        c_codes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        progress("\nStep 6: Cross-Category Deduplicator")

        a_codes = [c for c in a_codes if isinstance(c, dict)]
        b_codes = [c for c in b_codes if isinstance(c, dict)]
        c_codes = [c for c in c_codes if isinstance(c, dict)]

        def summarize(codes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return [{
                "code": c.get("code", ""),
                "name": str(c.get("name", ""))[:60],
                "definition": truncate_text(str(c.get("definition", "")), 150),
            } for c in codes]

        prompt = render_prompt_asset(
            "cross_category_dedup.md",
            a_codes=summarize(a_codes),
            b_codes=summarize(b_codes),
            c_codes=summarize(c_codes),
        )

        try:
            result = extract_json(self.client.chat(prompt))
            duplicates = result.get("duplicates_found", [])
            if duplicates:
                progress(f"  Duplicates found: {len(duplicates)}")

            to_remove: set[str] = set()
            for d in duplicates:
                remove = d.get("remove")
                if isinstance(remove, str) and remove:
                    to_remove.add(remove)
                elif isinstance(remove, list):
                    for r in remove:
                        if isinstance(r, str) and r:
                            to_remove.add(r)

            return {
                "category_a": [c for c in a_codes if c.get("code", "") not in to_remove],
                "category_b": [c for c in b_codes if c.get("code", "") not in to_remove],
                "category_c": [c for c in c_codes if c.get("code", "") not in to_remove],
                "duplicates_found": duplicates,
            }
        except Exception as e:  # noqa: BLE001
            progress(f"  [!] Deduplication error: {e}")
            return {
                "category_a": a_codes,
                "category_b": b_codes,
                "category_c": c_codes,
                "duplicates_found": [],
            }
