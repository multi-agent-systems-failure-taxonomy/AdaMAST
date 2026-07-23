"""The end-to-end taxonomy generation pipeline.

``TaxonomyPipeline`` orchestrates Steps 1-8 plus the optional max-codes
cap. It expects ``traces`` already in the unified format (use
:mod:`adamast.traces.loader` to get there) and returns a structured
taxonomy dict.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from adamast.learning.vendor.config import PipelineConfig, resolve_output_dir
from adamast.learning.vendor.llm import LLMClient, extract_json
from adamast.learning.vendor.pipeline.check import TaxonomyChecker
from adamast.learning.vendor.pipeline.dedup import CrossCategoryDeduplicator
from adamast.learning.vendor.pipeline.domain import SystemDomainAnalyzer
from adamast.learning.vendor.pipeline.generator import CategoryGenerator
from adamast.learning.vendor.pipeline.prompts import render_prompt_asset
from adamast.learning.vendor.pipeline.structure import TraceStructureExtractor
from adamast.learning.vendor.pipeline.validate import CrossCategoryValidator
from adamast.learning.vendor.traces.signals import SignalExtractor
from adamast.learning.vendor.utils import normalize_code_ids, progress, save_json

ADAMAST_VERSION = "1.0.0"


class TaxonomyPipeline:
    """Orchestrates the full 8-step taxonomy generation pipeline."""

    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        output_dir: Optional[Path | str] = None,
    ):
        self.config = config or PipelineConfig()
        self.output_dir = resolve_output_dir(output_dir)
        self.client = LLMClient(self.config.model, self.config.timeout)

        self.domain_info: Dict[str, Any] = {}
        self.structure_info: Dict[str, Any] = {}
        self.trace_signals: Dict[str, Any] = {}

    def run(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not traces:
            raise ValueError("No traces provided — pipeline cannot run on an empty input.")

        progress("=" * 70)
        progress(f"AdaMAST v{ADAMAST_VERSION} - Taxonomy Generation")
        progress("=" * 70)
        progress(f"Model: {self.config.model}")
        progress(f"Loaded {len(traces)} traces")

        # Step 1: Domain analysis
        progress("\n" + "=" * 50)
        self.domain_info = SystemDomainAnalyzer(self.client, self.config).analyze(traces)
        self._save_step("step1_domain_info", self.domain_info)

        # Step 2: Structure / agents / capabilities
        progress("\n" + "=" * 50)
        self.structure_info = TraceStructureExtractor(self.client, self.config).extract(traces)
        self._save_step("step2_structure_info", self.structure_info)

        # Step 2.5: Behavioral signals
        progress("\n" + "=" * 50)
        progress("Step 2.5: Trace Signal Extraction")
        self.trace_signals = SignalExtractor().extract(traces)
        self._save_step("step2_5_trace_signals", self.trace_signals)

        # Step 3: A codes
        progress("\n" + "=" * 50)
        a_codes = CategoryGenerator(
            self.client, self.config, "A",
            self.domain_info, self.structure_info, self.trace_signals,
        ).generate(traces)
        self._save_step("step3_a_codes", {"codes": a_codes})

        # Step 4: B codes
        progress("\n" + "=" * 50)
        b_codes = CategoryGenerator(
            self.client, self.config, "B",
            self.domain_info, self.structure_info,
        ).generate(traces, {"category_a": a_codes})
        self._save_step("step4_b_codes", {"codes": b_codes})

        # Step 5: C codes
        progress("\n" + "=" * 50)
        c_codes = CategoryGenerator(
            self.client, self.config, "C",
            self.domain_info, self.structure_info,
        ).generate(traces, {"category_a": a_codes, "category_b": b_codes})
        self._save_step("step5_c_codes", {"codes": c_codes})

        # Step 6: Cross-category dedup
        progress("\n" + "=" * 50)
        dedup_result = CrossCategoryDeduplicator(self.client).deduplicate(
            a_codes, b_codes, c_codes,
        )
        self._save_step("step6_dedup", dedup_result)

        # Step 6.5: Optional max-codes cap
        if self.config.max_codes > 0:
            total = sum(
                len(dedup_result.get(k, []))
                for k in ("category_a", "category_b", "category_c")
            )
            if total > self.config.max_codes:
                progress(f"\nStep 6.5: Max-codes cap ({total} -> {self.config.max_codes})")
                dedup_result = self._cap_codes(dedup_result, self.config.max_codes)
                self._save_step("step6_5_capped", dedup_result)

        # Step 7: Validation
        progress("\n" + "=" * 50)
        validated = CrossCategoryValidator(self.client, self.structure_info).validate(
            dedup_result.get("category_a", []),
            dedup_result.get("category_b", []),
            dedup_result.get("category_c", []),
        )
        self._save_step("step7_validated", validated)

        # Step 8: Final check + fix
        progress("\n" + "=" * 50)
        final = TaxonomyChecker(
            self.client, self.structure_info, self.domain_info
        ).check_and_fix(
            validated.get("category_a", []),
            validated.get("category_b", []),
            validated.get("category_c", []),
        )
        self._save_step("step8_final", final)

        taxonomy = self._build_taxonomy(final, len(traces))

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.output_dir / f"{self.config.output_filename_prefix}_{timestamp}.json"
        save_json(taxonomy, output_path)

        # Always also write a canonical ``taxonomy.json``.
        save_json(taxonomy, self.output_dir / "taxonomy.json")

        self._print_summary(taxonomy)
        return taxonomy

    # ───── Internals ─────

    def _save_step(self, name: str, data: Any) -> None:
        if not self.config.save_intermediate_steps:
            return
        save_json(data, self.output_dir / f"{name}.json")

    def _cap_codes(self, dedup_result: Dict[str, Any], max_codes: int) -> Dict[str, Any]:
        """Rank codes by importance and keep only the top N."""
        all_codes = (
            dedup_result.get("category_a", [])
            + dedup_result.get("category_b", [])
            + dedup_result.get("category_c", [])
        )

        summary = "\n".join(
            f"{c.get('code', '')}: {str(c.get('name', ''))[:60]} — {str(c.get('definition', ''))[:120]}"
            for c in all_codes
        )

        prompt = render_prompt_asset(
            "max_codes_cap.md",
            code_count=len(all_codes),
            max_codes=max_codes,
            summary=summary,
        )

        try:
            result = extract_json(self.client.chat(prompt))
            keep_ids = set(result.get("keep", [])[:max_codes])
        except Exception as e:  # noqa: BLE001
            progress(f"  [!] Max-codes cap LLM failed: {e}. Keeping first {max_codes} codes.")
            keep_ids = {c.get("code", "") for c in all_codes[:max_codes]}

        if len(keep_ids) < max_codes:
            for c in all_codes:
                if len(keep_ids) >= max_codes:
                    break
                keep_ids.add(c.get("code", ""))

        filtered = {
            "category_a": [c for c in dedup_result.get("category_a", []) if c.get("code") in keep_ids],
            "category_b": [c for c in dedup_result.get("category_b", []) if c.get("code") in keep_ids],
            "category_c": [c for c in dedup_result.get("category_c", []) if c.get("code") in keep_ids],
            "duplicates_found": dedup_result.get("duplicates_found", []),
            "max_codes_applied": max_codes,
        }
        kept = sum(len(filtered[k]) for k in ("category_a", "category_b", "category_c"))
        progress(
            f"  Kept {kept} codes "
            f"(A={len(filtered['category_a'])}, "
            f"B={len(filtered['category_b'])}, "
            f"C={len(filtered['category_c'])})"
        )
        return filtered

    def _build_taxonomy(self, final_codes: Dict[str, Any], n_traces: int) -> Dict[str, Any]:
        a_list = normalize_code_ids(final_codes.get("category_a", []), "A")
        b_list = normalize_code_ids(final_codes.get("category_b", []), "B")
        c_list = normalize_code_ids(final_codes.get("category_c", []), "C")

        def compact(code: Dict[str, Any]) -> Dict[str, Any]:
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
                "version": ADAMAST_VERSION,
                "timestamp": datetime.now().isoformat(),
                "model": self.config.model,
                "pipeline": "Architectural Risk + Behavioral Signals",
                "traces_analyzed": n_traces,
                "counts": {
                    "category_a": len(a_list),
                    "category_b": len(b_list),
                    "category_c": len(c_list),
                    "total": len(a_list) + len(b_list) + len(c_list),
                },
            },
            "category_definitions": {
                "A": "System-level failures (agent-independent).",
                "B": "Role-specific quality failures.",
                "C": "Domain-specific reasoning failures.",
            },
            "role_definitions": (
                self.structure_info.get("discovered_agents", {}).get("role_details", {})
            ),
            "generation_method": {
                "A": "Fully generated via architectural risk analysis + empirical behavioral signals",
                "B": "Fully generated via role guidance + architecture + capabilities context",
                "C": "Domain-seeded from error patterns + trace-grounded from observed reasoning flaws",
            },
            "annotation_layer": {
                "description": "Compact taxonomy for annotation judges — code, name, definition, severity only",
                "category_a": [compact(c) for c in a_list],
                "category_b": [compact(c) for c in b_list],
                "category_c": [compact(c) for c in c_list],
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
                "category_c": {c["code"]: c for c in c_list},
            },
        }

    def _print_summary(self, taxonomy: Dict[str, Any]) -> None:
        progress("\n" + "=" * 70)
        progress("TAXONOMY GENERATION COMPLETE")
        progress("=" * 70)

        counts = taxonomy["metadata"]["counts"]
        progress(f"\nCategory A (System):  {counts['category_a']} codes")
        progress(f"Category B (Role):    {counts['category_b']} codes")
        progress(f"Category C (Domain):  {counts['category_c']} codes")
        progress("-" * 40)
        progress(f"Total:                {counts['total']} codes")

        role_details = taxonomy.get("full_layer", {}).get("discovered_agents", {}).get("role_details", {})
        if role_details:
            progress("\nDiscovered Roles:")
            for role, details in role_details.items():
                agent_list = details.get("agents", [])
                if agent_list:
                    progress(f"  {role}: {len(agent_list)} agents")
