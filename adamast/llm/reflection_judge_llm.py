"""LLM bridge for the Reflection Judge.

Adapts adamast's existing transport (``learning_calls.support_model_call``,
which already supports Anthropic / OpenAI / Gemini via env config) to the
``LLMCall`` signature the reflection judge expects::

    llm_call(user_prompt, system_prompt, *, max_tokens, meter, warnings) -> dict

The judge surfaces parse failures and truncation diagnostics through the
optional ``warnings`` list — silently returning ``{}`` is much worse here than
in the simpler ``sonnet_json`` because the multi-stage pipeline early-returns
on empty analysis output.

The model identifier is provided to the bridge at construction time (no global
default). AdaMAST's transport infers the provider from the model string
prefix (``claude`` / ``anthropic`` / ``gemini`` / else OpenAI-compat).
"""

from __future__ import annotations

import json as _json
import re as _re
from typing import Callable, Optional

from adamast.llm.learning_calls import support_model_call
from adamast.core.taxonomy_data import CostMeter

_JSON_RE = _re.compile(r"\{[\s\S]*\}")


def make_llm_call(model: str, *, transport: Optional[Callable[[str, str], Optional[str]]] = None
                  ) -> Callable[..., dict]:
    """Build an LLM caller bound to a specific model.

    Parameters
    ----------
    model
        Model id passed through to ``transport`` on every call.
    transport
        Function ``(combined_prompt, model) -> raw_text | None`` matching
        ``learning_calls.support_model_call``'s signature. Override for tests.

    Returns
    -------
    callable
        ``f(user_prompt, system_prompt, *, max_tokens=8192, meter=None,
        warnings=None)`` returning a parsed JSON dict (``{}`` on parse failure;
        diagnostics appended to ``warnings`` when supplied).
    """
    call = transport or support_model_call

    def llm_call(prompt: str, system: str, *, max_tokens: int = 8192,
                 meter: Optional[CostMeter] = None,
                 warnings: Optional[list] = None) -> dict:
        # adamast's transports take a single combined prompt; system is
        # concatenated with a separator so model providers that don't expose a
        # system slot still receive both pieces.
        combined = f"{system}\n\n{prompt}" if system else prompt
        try:
            raw = call(combined, model)
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"LLM call raised: {type(exc).__name__}: {exc}")
            return {}
        if not raw:
            if warnings is not None:
                warnings.append("LLM returned empty response")
            return {}

        # CostMeter is currently informational only; adamast's transports
        # don't surface per-call USD cost. Left unmeasured to avoid faking a
        # number. Hook a real cost source here if/when one is added.
        _ = meter

        text = raw.strip()
        for candidate in (text, _JSON_RE.search(text).group(0) if _JSON_RE.search(text) else ""):
            if not candidate:
                continue
            try:
                data = _json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except (_json.JSONDecodeError, TypeError):
                continue

        if warnings is not None:
            warnings.append(
                f"JSON parse failed (output={len(text)} chars). "
                f"Last 80 chars: {text[-80:]!r}"
            )
        return {}

    return llm_call


__all__ = ["make_llm_call"]
