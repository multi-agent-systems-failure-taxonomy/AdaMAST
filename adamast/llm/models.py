"""Recognized AdaMAST model profiles used for adaptive judge batching."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files


_MODEL_PROFILES = json.loads(
    files(__package__).joinpath("assets").joinpath("model_profiles.json").read_text(
        encoding="utf-8"
    )
)
_REGION_PREFIXES = tuple(_MODEL_PROFILES["region_prefixes"])


@dataclass(frozen=True)
class ModelProfile:
    context_tokens: int
    output_reserve_tokens: int = 8_192
    safety_ratio: float = 0.90


def resolve_model_profile(model: str) -> ModelProfile:
    """Resolve a conservative context profile from a recognized model family.

    Recognizes plain model ids (``claude-sonnet-4-5``) as well as Bedrock
    inference-profile shapes that carry a region prefix
    (``us.anthropic.claude-sonnet-4-5-...``, ``eu.anthropic.claude-...``) and
    the bare ``anthropic.claude-...`` form. All of these route to the Claude
    context profile.
    """
    name = (model or "").strip().lower()
    if not name:
        raise ValueError("adamast_model is required")
    if _is_anthropic_model(name):
        return ModelProfile(context_tokens=_MODEL_PROFILES["anthropic_context_tokens"])
    for profile in _MODEL_PROFILES["prefix_profiles"]:
        if name.startswith(tuple(profile["prefixes"])):
            return ModelProfile(context_tokens=profile["context_tokens"])
    raise ValueError(f"unrecognized AdaMAST model {model!r}")


def _is_anthropic_model(name: str) -> bool:
    """True for plain Claude ids AND Bedrock inference-profile ids."""
    if name.startswith(("claude", "anthropic", "anthropic.claude")):
        return True
    if name.startswith("bedrock/") and "anthropic" in name:
        return True
    for prefix in _REGION_PREFIXES:
        if name.startswith(prefix) and "anthropic.claude" in name:
            return True
    return False


def is_anthropic_model(model: str) -> bool:
    """Public predicate — True iff the model id routes to a Claude transport."""
    return _is_anthropic_model((model or "").strip().lower())


def is_bedrock_model(model: str) -> bool:
    """True iff the model id looks like an AWS Bedrock inference profile.

    Used by the LLM transport to choose Bedrock-specific handling over the
    vanilla ``Anthropic()`` client. Bearer-token Bedrock auth routes through
    boto3 Converse; standard AWS credentials/profile may use the provider SDK.
    """
    name = (model or "").strip().lower()
    if not name:
        return False
    for prefix in _REGION_PREFIXES:
        if name.startswith(prefix) and "anthropic" in name:
            return True
    return name.startswith("bedrock/") or name.startswith("anthropic.")


def estimate_tokens(text: str) -> int:
    """Conservative dependency-free estimate for packing, not billing."""
    return max(1, (len(text.encode("utf-8")) + 2) // 3)
