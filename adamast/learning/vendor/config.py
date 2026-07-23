"""Pipeline configuration for AdaMAST taxonomy generation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _default_model_for_endpoint() -> str:
    """Pick a sensible default model based on the configured OpenAI base URL.

    When the OpenAI-compatible client is pointed at a non-OpenAI shim (Gemini,
    Anthropic), the OpenAI default ``gpt-5-nano`` will 404 silently. This
    helper inspects ``OPENAI_BASE_URL`` / ``OPENAI_API_BASE`` and selects a
    matching default. ``OPENAI_MODEL`` always wins when set.
    """
    base = (os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or "").lower()
    if "generativelanguage.googleapis.com" in base or "gemini" in base:
        return "gemini-3-flash-preview"
    if "anthropic.com" in base:
        return "claude-haiku-4-7"
    return "gpt-5-nano"


@dataclass
class PipelineConfig:
    """Runtime configuration for the taxonomy generation pipeline.

    Most fields have sensible defaults. The two settings most users care
    about are ``model`` (which LLM to call) and ``max_codes`` (cap on the
    final taxonomy size).
    """

    # LLM settings
    model: str = field(default_factory=lambda: os.getenv("ADAMAST_MODEL") or _default_model_for_endpoint())
    timeout: int = 180
    max_workers: int = 8

    # Sampling
    traces_for_analysis: int = 20
    traces_per_agent: int = 50

    # Pipeline behavior
    enable_parallel: bool = True
    max_codes: int = 0  # 0 = no cap

    # Output controls
    save_intermediate_steps: bool = True
    output_filename_prefix: str = "taxonomy"

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """Build a config primarily from environment variables.

        Recognized vars: ``ADAMAST_MODEL``, ``ADAMAST_MAX_WORKERS``,
        ``ADAMAST_MAX_CODES``, ``ADAMAST_TIMEOUT``.
        """
        return cls(
            model=os.getenv("ADAMAST_MODEL") or _default_model_for_endpoint(),
            timeout=int(os.getenv("ADAMAST_TIMEOUT", "180")),
            max_workers=int(os.getenv("ADAMAST_MAX_WORKERS", "8")),
            max_codes=int(os.getenv("ADAMAST_MAX_CODES", "0")),
        )


def resolve_output_dir(output_dir: Optional[Path | str]) -> Path:
    """Resolve and create an output directory; default to ``./adamast_output``."""
    if output_dir is None:
        output_dir = Path.cwd() / "adamast_output"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir
