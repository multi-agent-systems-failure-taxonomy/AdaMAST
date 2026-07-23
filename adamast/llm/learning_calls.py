"""Model-facing learning calls for generation and refinement.

This module intentionally owns only prompt-boundary formatting, provider
transport, JSON parsing, and bounded repair-retry. Taxonomy lifecycle,
support tiers, archival, storage, and triggering remain owned elsewhere.
"""

from __future__ import annotations

import json
import logging
import os
from importlib import resources
from string import Template
from typing import Any, Callable

logger = logging.getLogger(__name__)

ANTHROPIC_OPENAI_MAX_TOKENS = 8192
GEMINI_MAX_OUTPUT_TOKENS = 16384
DEFAULT_MAX_RETRIES = 1
DEFAULT_EXCERPT_CAP = 25000
SUPPORT_TRACE_MARKERS = (
    ("TOOL FAILURE", "PostToolUseFailure", False),
    ("AGENT REFLECTION", "AdaMAST reflection:", True),
    ("FINAL GATE", "Final AdaMAST status:", True),
    ("FINAL ANSWER", "<FINAL_ANSWER>", True),
    ("ARITHMETIC", "\"name\":\"Bash\"", True),
)

ModelCall = Callable[[str, str], str | None]

REFINEMENT_PROMPT = (
    resources.files("adamast.llm")
    .joinpath("assets").joinpath("standard_refinement_prompt.md")
    .read_text(encoding="utf-8")
)


def outcome_blind_trace(record: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical generation fields without outcome boundary data."""
    metadata = record.get("metadata")
    clean_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"outcome", "final_gate_status"}
    } if isinstance(metadata, dict) else {}
    return {
        "problem_id": record.get("problem_id"),
        "task": record.get("task", ""),
        "raw_trajectory": record.get("raw_trajectory", ""),
        "metadata": clean_metadata,
    }


def format_support_trace(
    record: dict[str, Any],
    cap: int | None = None,
) -> str:
    """Outcome-blind support sample from across the trace.

    The cap is taken from ``ADAMAST_JUDGE_CAP`` (chars) when set, else from the
    explicit ``cap`` argument, else from ``DEFAULT_EXCERPT_CAP``. Setting
    ``ADAMAST_JUDGE_CAP=0`` or any value larger than the trace returns the
    entire trajectory — useful for diagnostic full-trace judging on a small
    fixture set.
    """
    text = str(record.get("raw_trajectory") or "")
    if not text:
        return "(no trace text)"
    env_cap = os.environ.get("ADAMAST_JUDGE_CAP")
    if env_cap is not None:
        try:
            cap = int(env_cap)
        except ValueError:
            cap = DEFAULT_EXCERPT_CAP
    elif cap is None:
        cap = DEFAULT_EXCERPT_CAP
    if cap <= 0 or len(text) <= cap:
        return text

    segments: list[tuple[str, int, int]] = []
    head_size = min(2000, cap // 5)
    tail_size = min(3000, cap // 4)
    segments.append(("TRACE START", 0, head_size))

    marker_budget = max(0, cap - head_size - tail_size - 600)
    marker_size = max(600, marker_budget // len(SUPPORT_TRACE_MARKERS))
    for label, marker, use_last in SUPPORT_TRACE_MARKERS:
        position = text.rfind(marker) if use_last else text.find(marker)
        if position < 0:
            continue
        start = max(0, position - marker_size // 3)
        segments.append((label, start, min(len(text), start + marker_size)))

    segments.append(("TRACE END", max(0, len(text) - tail_size), len(text)))
    rendered: list[str] = []
    seen_ranges: list[tuple[int, int]] = []
    for label, start, end in segments:
        if any(start >= old_start and end <= old_end for old_start, old_end in seen_ranges):
            continue
        seen_ranges.append((start, end))
        rendered.append(f"\n--- {label} [{start}:{end}] ---\n{text[start:end]}")
    return "".join(rendered)[:cap]


def format_refinement_traces(
    records: list[dict[str, Any]],
    cap_per_trace: int = 1200,
) -> str:
    """Outcome-blind refinement excerpts copied at the new trace-schema seam."""
    if not records:
        return "  (no recent trace evidence)"
    blocks = []
    for record in records:
        clean = outcome_blind_trace(record)
        blocks.append(
            f"### {clean['problem_id']}\n"
            f"task: {clean['task']}\n"
            f"transcript excerpt:\n"
            f"{str(clean['raw_trajectory'])[:cap_per_trace]}"
        )
    return "\n\n".join(blocks)


def build_refinement_prompt(
    current: dict[str, Any],
    records: list[dict[str, Any]],
) -> str:
    """Build the corrected refiner prompt at the canonical-schema seam."""
    return Template(REFINEMENT_PROMPT).substitute(
        existing_taxonomy=json.dumps(current, ensure_ascii=False),
        trace_excerpts=format_refinement_traces(records),
    )


def parse_json_object(text: Any) -> dict[str, Any] | None:
    """Fence-strip plus JSON-object salvage."""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3].strip()
    if text.lower().startswith("json"):
        text = text[4:].strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if 0 <= start < end:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _anthropic_client(model: str):
    """Return the right Anthropic SDK client for the model id.

    AWS Bedrock inference profiles (``us.anthropic.claude-...``,
    ``eu.anthropic.claude-...``, ``anthropic.claude-...``, ``bedrock/...``)
    go through ``AnthropicBedrock`` which reads ``AWS_BEARER_TOKEN_BEDROCK``
    or standard AWS credentials. Everything else goes through the vanilla
    ``Anthropic()`` client which reads ``ANTHROPIC_API_KEY``.

    Returns ``None`` if the ``anthropic`` package is not installed.
    """
    from .models import is_bedrock_model

    if is_bedrock_model(model):
        try:
            from anthropic import AnthropicBedrock
        except ImportError:
            logger.error(
                "anthropic package not installed (AnthropicBedrock required "
                "for Bedrock model id %r). Install with: pip install "
                "'adamast[anthropic]'",
                model,
            )
            return None
        return AnthropicBedrock()
    try:
        from anthropic import Anthropic
    except ImportError:
        logger.error("anthropic package not installed")
        return None
    return Anthropic()


def _bedrock_converse_call(
    prompt: str,
    model: str,
    *,
    max_tokens: int,
    temperature: float,
    system: str | None = None,
) -> str | None:
    """Call AWS Bedrock Converse through boto3.

    boto3/botocore can read ``AWS_BEARER_TOKEN_BEDROCK`` directly from the
    environment, which is the credential form Claude Code's Bedrock mode uses.
    The dependency is imported lazily so non-Bedrock users do not need boto3.
    """
    try:
        import boto3
    except ImportError:
        logger.error(
            "boto3 package not installed (required for AWS_BEARER_TOKEN_BEDROCK "
            "Bedrock auth). Install with: pip install -U boto3"
        )
        return None
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        logger.error("AWS_REGION or AWS_DEFAULT_REGION is required for Bedrock")
        return None
    try:
        from botocore.config import Config

        # Long generations on big prompts routinely exceed botocore's default
        # 60s read timeout, silently degrading a judge pass to None. Allow
        # slow responses and retry transient failures.
        client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                connect_timeout=10,
                read_timeout=300,
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
        )
        kwargs: dict[str, Any] = {
            "modelId": _bedrock_model_id(model),
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system:
            kwargs["system"] = [{"text": system}]
        response = client.converse(**kwargs)
        content = response["output"]["message"]["content"]
        return "".join(block.get("text", "") for block in content)
    except Exception as exc:  # noqa: BLE001
        logger.error("bedrock converse call failed: %s", exc)
        return None


def _bedrock_model_id(model: str) -> str:
    name = (model or "").strip()
    return name.split("/", 1)[1] if name.lower().startswith("bedrock/") else name


def support_model_call(prompt: str, model: str) -> str | None:
    """Corrected support-judge transport with explicit output caps.

    Routes Claude / Anthropic model ids through the Anthropic SDK, bearer-token
    Bedrock model ids through boto3 Converse, Gemini ids through the Google
    REST API, everything else through the OpenAI client (which honors
    ``OPENAI_BASE_URL`` for OpenAI-compatible local endpoints).
    """
    from .models import is_anthropic_model, is_bedrock_model

    use_openai_endpoint = bool(os.environ.get("OPENAI_BASE_URL"))
    if is_anthropic_model(model) and not use_openai_endpoint:
        if is_bedrock_model(model) and os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
            return _bedrock_converse_call(
                prompt,
                model,
                max_tokens=ANTHROPIC_OPENAI_MAX_TOKENS,
                temperature=0.0,
                system="Output ONLY valid JSON. No markdown.",
            )
        client = _anthropic_client(model)
        if client is None:
            return None
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=ANTHROPIC_OPENAI_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(block.text for block in msg.content if hasattr(block, "text"))
        except Exception as exc:  # noqa: BLE001
            logger.error("anthropic judge call failed: %s", exc)
            return None
    if model.startswith("gemini"):
        import urllib.error
        import urllib.request

        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            logger.error("GEMINI_API_KEY not set")
            return None
        model_id = model.split("/", 1)[-1]
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_id}:generateContent?key={key}"
        )
        body = json.dumps(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.0,
                    "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS,
                    "responseMimeType": "application/json",
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
            return "".join(
                part.get("text", "")
                for part in data["candidates"][0]["content"]["parts"]
            )
        except (
            urllib.error.URLError,
            KeyError,
            IndexError,
            TimeoutError,
        ) as exc:
            logger.error("gemini judge call failed: %s", exc)
            return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai package not installed")
        return None
    try:
        response = OpenAI().chat.completions.create(
            model=model,
            temperature=0.0,
            max_tokens=ANTHROPIC_OPENAI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": "Output ONLY valid JSON. No markdown."},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        logger.error("openai judge call failed: %s", exc)
        return None


def judge_json(
    prompt: str,
    model: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    call: ModelCall | None = None,
) -> dict[str, Any] | None:
    """Corrected support JSON repair-retry."""
    function = call or support_model_call
    attempt_prompt = prompt
    for attempt in range(max_retries + 1):
        raw = function(attempt_prompt, model)
        parsed = parse_json_object(raw)
        if parsed is not None:
            return parsed
        if attempt < max_retries:
            attempt_prompt = (
                prompt + "\n\nYour previous reply was NOT valid JSON. Reply with "
                "ONLY the JSON object specified above, nothing else."
            )
    return None


def refinement_model_call(prompt: str, model: str) -> str | None:
    """Corrected refiner transport with explicit output caps.

    Routes Claude / Anthropic model ids through the Anthropic SDK and
    bearer-token Bedrock model ids through boto3 Converse.
    """
    from .models import is_anthropic_model, is_bedrock_model

    use_openai_endpoint = bool(os.environ.get("OPENAI_BASE_URL"))
    if is_anthropic_model(model) and not use_openai_endpoint:
        if is_bedrock_model(model) and os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
            return _bedrock_converse_call(
                prompt,
                model,
                max_tokens=ANTHROPIC_OPENAI_MAX_TOKENS,
                temperature=0.3,
                system="Output ONLY valid JSON. No markdown fences.",
            )
        client = _anthropic_client(model)
        if client is None:
            return None
        try:
            message = client.messages.create(
                model=model,
                max_tokens=ANTHROPIC_OPENAI_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(
                block.text for block in message.content if hasattr(block, "text")
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("anthropic refiner call failed: %s", exc)
            return None
    if model.startswith("gemini"):
        import urllib.error
        import urllib.request

        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            logger.error("GEMINI_API_KEY not set")
            return None
        model_id = model.split("/", 1)[-1]
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_id}:generateContent?key={key}"
        )
        body = json.dumps(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS,
                    "responseMimeType": "application/json",
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            logger.error(
                "gemini HTTP %s for model %s: %s",
                exc.code,
                model_id,
                detail,
            )
            return None
        except (urllib.error.URLError, TimeoutError) as exc:
            logger.error("gemini request failed: %s", exc)
            return None
        try:
            return "".join(
                part.get("text", "")
                for part in data["candidates"][0]["content"]["parts"]
            )
        except (KeyError, IndexError):
            logger.error("gemini response missing candidates: %s", str(data)[:300])
            return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai package not installed")
        return None
    try:
        response = OpenAI().chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "Output ONLY valid JSON. No markdown fences.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=ANTHROPIC_OPENAI_MAX_TOKENS,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        logger.error("openai refiner call failed: %s", exc)
        return None


def refine_json(
    prompt: str,
    *,
    model: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    call: ModelCall | None = None,
) -> dict[str, Any] | None:
    """Corrected refiner JSON repair-retry."""
    function = call or refinement_model_call
    attempt_prompt = prompt
    for attempt in range(max_retries + 1):
        raw = function(attempt_prompt, model)
        parsed = parse_json_object(raw)
        if parsed is not None:
            return parsed
        if attempt < max_retries:
            attempt_prompt = (
                prompt + "\n\nYour previous reply was NOT valid JSON. Reply with "
                "ONLY the JSON object specified above, nothing else."
            )
    return None
