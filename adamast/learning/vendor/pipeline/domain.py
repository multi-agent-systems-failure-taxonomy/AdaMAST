"""Step 1: SystemDomainAnalyzer.

Reads a stratified sample of traces and asks the LLM to characterize the
task domain — what kind of problem this MAS is solving, what makes it
hard, what domain-specific terminology it uses, and what common error
patterns are characteristic. The output feeds C-code generation, which
needs domain context to surface reasoning failures.
"""

from __future__ import annotations

from typing import Any, Dict, List

from adamast.learning.vendor.config import PipelineConfig
from adamast.learning.vendor.llm import LLMClient, extract_json
from adamast.learning.vendor.pipeline.prompts import render_prompt_asset
from adamast.learning.vendor.utils import format_trace_for_prompt, progress, stratified_sample


class SystemDomainAnalyzer:
    """Analyze traces to extract domain knowledge that informs C-code generation."""

    def __init__(self, client: LLMClient, config: PipelineConfig):
        self.client = client
        self.config = config

    def analyze(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        progress("Step 1: System Domain Analyzer")

        sample = stratified_sample(traces, self.config.traces_for_analysis)
        traces_text = "\n\n".join(format_trace_for_prompt(t, max_length=3000) for t in sample)

        prompt = render_prompt_asset("domain_analysis.md", traces_text=traces_text)

        response = self.client.chat(prompt)
        result = extract_json(response)

        progress(f"  Domain: {result.get('domain', {}).get('name', 'Unknown')}")
        progress(f"  Subdomains: {result.get('subdomains', [])}")
        progress(f"  Error patterns: {len(result.get('common_error_patterns', []))}")

        return result
