"""Classify a single trace against a generated taxonomy.

This is the runtime side of AdaMAST: once you have a taxonomy from
:class:`adamast.pipeline.TaxonomyPipeline`, you can use ``TaxonomyClassifier``
to diagnose new failure traces by picking the single best-matching code.

The classifier is intentionally minimal — it does not maintain cross-task
patterns or modify instructions. Those policies live in the harness that
embeds the classifier, not in the taxonomy library.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Union

from adamast.learning.vendor.config import PipelineConfig
from adamast.learning.vendor.llm import LLMClient, extract_json
from adamast.learning.vendor.utils import format_trace_for_prompt

logger = logging.getLogger(__name__)


@dataclass
class Diagnosis:
    """Result of classifying a trace against a taxonomy."""

    code: str
    label: str
    category: str
    evidence: str = ""
    confidence: float = 0.0
    recovery_hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "category": self.category,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "recovery_hint": self.recovery_hint,
        }


class TaxonomyClassifier:
    """Classify failure traces against a previously-generated taxonomy.

    Construct from a taxonomy dict (returned by ``TaxonomyPipeline.run``)
    or from a path to a ``taxonomy.json``. Then call :meth:`classify` with
    a single trace dict.
    """

    def __init__(
        self,
        taxonomy: Union[Dict[str, Any], str, Path],
        config: Optional[PipelineConfig] = None,
    ):
        self.config = config or PipelineConfig()
        self.client = LLMClient(self.config.model, self.config.timeout)

        if isinstance(taxonomy, (str, Path)):
            taxonomy = json.loads(Path(taxonomy).read_text(encoding="utf-8"))
        self.taxonomy = taxonomy
        self.codes = _flatten_codes(taxonomy)

        if not self.codes:
            raise ValueError("Taxonomy contains no codes — cannot classify.")

    def classify(self, trace: Dict[str, Any]) -> Optional[Diagnosis]:
        """Classify ``trace`` and return the best-matching diagnosis, or None on failure."""
        taxonomy_desc = self._format_codes_for_prompt()
        trace_text = format_trace_for_prompt(trace, max_length=30000)

        prompt = _render_classifier_asset(
            "classifier_user.md",
            taxonomy_desc=taxonomy_desc,
            trace_text=trace_text,
        )

        try:
            response = self.client.chat(
                prompt,
                system=_classifier_asset("classifier_system.md"),
            )
            data = extract_json(response)
            if not data:
                return None

            code = data.get("code", "")
            if not _has_code(self.codes, code):
                code = self._closest_code(code)

            return Diagnosis(
                code=code,
                label=data.get("label", ""),
                category=code[0] if code else "",
                evidence=data.get("evidence", ""),
                confidence=float(data.get("confidence", 0.5) or 0.5),
                recovery_hint=data.get("recovery_hint", ""),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Classification failed: %s", e)
            return None

    def classify_batch(self, traces: List[Dict[str, Any]]) -> List[Optional[Diagnosis]]:
        """Classify multiple traces sequentially. Returns a list aligned with input."""
        return [self.classify(t) for t in traces]

    # ───── Helpers ─────

    def _format_codes_for_prompt(self) -> str:
        return "\n".join(
            f"  {c['code']}: {c.get('name', c.get('label', ''))} — {c.get('definition', c.get('description', ''))}"
            for c in self.codes
        )

    def _closest_code(self, code: str) -> str:
        if not self.codes:
            return code
        cat = code[0] if code else ""
        same_cat = [c["code"] for c in self.codes if c["code"].startswith(cat)]
        if same_cat:
            return same_cat[0]
        return self.codes[0]["code"]


def _flatten_codes(taxonomy: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull every code out of a taxonomy dict, regardless of layer/format.

    Looks at ``full_layer`` first (which has all the metadata), then
    ``annotation_layer``, then a flat ``codes`` list, then bare
    ``category_a/b/c`` keys.
    """
    codes: List[Dict[str, Any]] = []

    layers = (
        taxonomy.get("full_layer"),
        taxonomy.get("annotation_layer"),
        taxonomy,
    )
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        for cat_key, cat_letter in (("category_a", "A"), ("category_b", "B"), ("category_c", "C")):
            data = layer.get(cat_key)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        codes.append({
                            "code": item.get("code", f"{cat_letter}?"),
                            "name": item.get("name", item.get("label", "")),
                            "label": item.get("name", item.get("label", "")),
                            "category": cat_letter,
                            "definition": item.get("definition", item.get("description", "")),
                            "description": item.get("definition", item.get("description", "")),
                        })
            elif isinstance(data, dict):
                for code_id, info in data.items():
                    if not isinstance(info, dict):
                        continue
                    codes.append({
                        "code": code_id,
                        "name": info.get("name", info.get("label", "")),
                        "label": info.get("name", info.get("label", "")),
                        "category": cat_letter,
                        "definition": info.get("definition", info.get("description", "")),
                        "description": info.get("definition", info.get("description", "")),
                    })
        if codes:
            break

    if not codes:
        flat = taxonomy.get("codes", [])
        if isinstance(flat, list):
            for item in flat:
                if isinstance(item, dict):
                    codes.append(item)

    # Deduplicate by code id while preserving order
    seen = set()
    unique: List[Dict[str, Any]] = []
    for c in codes:
        cid = c.get("code", "")
        if cid in seen:
            continue
        seen.add(cid)
        unique.append(c)
    return unique


def _has_code(codes: List[Dict[str, Any]], code_id: str) -> bool:
    return any(c.get("code") == code_id for c in codes)


def _classifier_asset(name: str) -> str:
    return (
        files("adamast.learning.vendor")
        .joinpath("assets").joinpath(name)
        .read_text(encoding="utf-8")
    )


def _render_classifier_asset(name: str, **context: str) -> str:
    return Template(_classifier_asset(name)).substitute(context)
