"""Drive one LLM agent conversation through the AdaMAST lifecycle."""

from __future__ import annotations

import re
import sys
import uuid
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Callable

from adamast import (
    GenerationTrace,
    ReflectionResult,
    SessionEndResult,
    end_session,
    pin_gate_decision,
    pre_submission,
    record_trace,
    redact_trace,
    start_session,
)
from adamast.protocol.checkpoint_prompt import (
    render_format_repair,
    render_reflection_prompt,
)
from adamast.core.evidence import record_reflection
from adamast.core.reflection import harvest_reflection
from adamast.core.traces import DEFAULT_TRACE_ROOT
from adamast.core import resolver, store

MessageCall = Callable[[list[dict[str, str]]], str]

CHECKPOINT_REQUEST = re.compile(
    r"AdaMAST\s+checkpoint\s+request\s*:\s*(.+)",
    re.IGNORECASE,
)

STANDING_PROMPT = (
    resources.files("adamast.hosts.single_llm")
    .joinpath("assets").joinpath("standing_prompt.md")
    .read_text(encoding="utf-8")
)


@dataclass(frozen=True)
class SingleLLMConfig:
    trace_output: Path
    adamast_model: str
    store_dir: Path = store.DEFAULT_STORE_DIR
    trace_root: Path = DEFAULT_TRACE_ROOT
    inherit: str | None = None
    max_retries: int = 3
    format_retries: int = 2
    repair_rounds: int | None = None
    max_checkpoints: int = 20
    dashboard: bool = True
    repo: str | None = None
    repo_path: Path | None = None
    generation_threshold: int = 5
    k_init: int = 10
    k: int = 20
    generation_stops: bool = False
    skip_judge: bool = False
    refinement_stops: bool = False
    advanced_refinement: bool = False
    freeze: bool = False
    evidence_export: Path | None = None
    redact_traces: bool = True
    gate_exhaustion_policy: str = "raise"
    recent_activity_messages: int = 8
    recent_activity_chars: int = 12000

    def __post_init__(self) -> None:
        if self.gate_exhaustion_policy not in {"raise", "release"}:
            raise ValueError("gate_exhaustion_policy must be 'raise' or 'release'")
        if self.recent_activity_messages <= 0:
            raise ValueError("recent_activity_messages must be positive")
        if self.recent_activity_chars <= 0:
            raise ValueError("recent_activity_chars must be positive")
        # max_retries is the legacy shared knob; it maps to the substantive
        # repair budget when repair_rounds is not set explicitly.
        if self.repair_rounds is None:
            object.__setattr__(self, "repair_rounds", self.max_retries)
        if self.repair_rounds < 0:
            raise ValueError("repair_rounds cannot be negative")
        if self.format_retries < 0:
            raise ValueError("format_retries cannot be negative")
        for name, value in (
            ("generation_threshold", self.generation_threshold),
            ("k_init", self.k_init),
            ("k", self.k),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class SingleLLMResult:
    answer: str
    gate_text: str
    checkpoint_count: int
    messages: tuple[dict[str, str], ...]
    session_end: SessionEndResult
    gate_allowed: bool = True


def run_single_llm(
    task: str,
    call: MessageCall,
    config: SingleLLMConfig,
    *,
    problem_id: str | None = None,
) -> SingleLLMResult:
    """Run one no-harness LLM agent with dynamic AdaMAST checkpoints."""
    if not task.strip():
        raise ValueError("task cannot be empty")
    inherit = config.inherit if config.inherit is not None else resolver.ABSENT
    run_id = problem_id or f"single-llm:{uuid.uuid4().hex}"
    session = start_session(
        inherit,
        trace_output=config.trace_output,
        store_dir=config.store_dir,
        trace_root=config.trace_root,
        session_id=run_id,
        adamast_model=config.adamast_model,
        max_retries=config.repair_rounds,
        dashboard=config.dashboard,
        repo=config.repo,
        repo_path=config.repo_path,
        generation_threshold=config.generation_threshold,
        k_init=config.k_init,
        k=config.k,
        generation_stops=config.generation_stops,
        skip_judge=config.skip_judge,
        refinement_stops=config.refinement_stops,
        advanced_refinement=config.advanced_refinement,
        freeze=config.freeze,
        evidence_export=config.evidence_export,
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": STANDING_PROMPT},
        {"role": "user", "content": task},
    ]
    checkpoint_count = 0
    repair_attempts = 0
    segment_start = 1
    answer = ""
    gate_text = ""
    try:
        while True:
            answer = _call(call, messages)
            messages.append({"role": "assistant", "content": answer})
            marker = CHECKPOINT_REQUEST.search(answer)
            if marker:
                checkpoint_count += 1
                if checkpoint_count > config.max_checkpoints:
                    raise RuntimeError(
                        f"single-LLM checkpoint limit exceeded "
                        f"({config.max_checkpoints})"
                    )
                recent = _render_recent_messages(
                    messages[segment_start:],
                    max_messages=config.recent_activity_messages,
                    max_chars=config.recent_activity_chars,
                )
                reflection = _collect_reflection(
                    call,
                    messages,
                    session.delivery.taxonomy_id,
                    session.delivery.taxonomy,
                    recent_activity=recent,
                    gate_label="major-segment checkpoint",
                    full=False,
                    format_retries=config.format_retries,
                )
                _record(
                    config,
                    run_id,
                    session.delivery.taxonomy_id,
                    reflection,
                    gate="single_llm_checkpoint",
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "AdaMAST checkpoint accepted. Apply the reflected "
                            "change only if Decide required one, then continue "
                            "the original task."
                        ),
                    }
                )
                segment_start = len(messages) - 1
                continue

            reflection, gate_text, pinned_status = _collect_final_gate(
                call,
                messages,
                session,
                format_retries=config.format_retries,
                repair_attempts_used=repair_attempts,
                recent_activity_messages=config.recent_activity_messages,
                recent_activity_chars=config.recent_activity_chars,
            )
            _record(
                config,
                run_id,
                session.delivery.taxonomy_id,
                reflection,
                gate="single_llm_stop",
            )
            decision, flipped = pin_gate_decision(
                pre_submission(
                    session,
                    gate_text,
                    repair_attempts_used=repair_attempts,
                ),
                pinned_status,
                max_retries=config.repair_rounds,
            )
            if flipped:
                print(
                    "[adamast] single-llm verdict flip suppressed: gated on "
                    f"pre-re-prompt status {pinned_status}",
                    file=sys.stderr,
                )
            if decision.decision == "approve_unresolved":
                if config.gate_exhaustion_policy == "raise":
                    raise RuntimeError("AdaMAST final repair limit exceeded")
                break
            if decision.allow:
                break
            repair_attempts += 1
            if repair_attempts > config.repair_rounds:
                if config.gate_exhaustion_policy == "raise":
                    raise RuntimeError("AdaMAST final repair limit exceeded")
                break
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"AdaMAST blocked completion: {decision.reason}. "
                        "Perform the focused repair from Decide, verify it, "
                        "and return a corrected proposed final answer."
                    ),
                }
            )

        trace = GenerationTrace(
            problem_id=run_id,
            task=task,
            raw_trajectory=_render_messages(messages),
            metadata={
                "harness": "single_llm",
                "taxonomy_id": session.delivery.taxonomy_id,
                "checkpoint_count": checkpoint_count,
            },
        )
        if config.redact_traces:
            trace = redact_trace(trace)
        record_trace(session, trace)
        ended = end_session(session)
        return SingleLLMResult(
            answer=answer,
            gate_text=gate_text,
            checkpoint_count=checkpoint_count,
            messages=tuple(messages),
            session_end=ended,
            gate_allowed=decision.decision == "approve",
        )
    except Exception:
        if not session._ended:
            session.workspace.finish_session(session.session_id)
            session._ended = True
        raise


def _collect_final_gate(
    call,
    messages,
    session,
    *,
    format_retries,
    repair_attempts_used,
    recent_activity_messages,
    recent_activity_chars,
):
    return _collect_reflection(
        call,
        messages,
        session.delivery.taxonomy_id,
        session.delivery.taxonomy,
        recent_activity=_render_recent_messages(
            messages,
            max_messages=recent_activity_messages,
            max_chars=recent_activity_chars,
        ),
        gate_label="final submission gate",
        full=True,
        format_retries=format_retries,
        return_text=True,
        prompt_suffix=(
            "\nThe runtime-counted value for `Repair attempts used:` is "
            f"{repair_attempts_used}. Emit that exact integer."
        ),
    )


def _collect_reflection(
    call: MessageCall,
    messages: list[dict[str, str]],
    taxonomy_id: str,
    taxonomy: dict,
    *,
    recent_activity: str,
    gate_label: str,
    full: bool,
    format_retries: int,
    return_text: bool = False,
    prompt_suffix: str = "",
):
    checkpoint_id = uuid.uuid4().hex
    prompt = render_reflection_prompt(
        taxonomy_id=taxonomy_id,
        codes=taxonomy["codes"],
        checkpoint_id=checkpoint_id,
        gate_label=gate_label,
        recent_activity=recent_activity,
        full=full,
    ) + prompt_suffix
    known = [str(code["id"]) for code in taxonomy["codes"]]
    pinned_status: str | None = None
    for attempt in range(max(0, format_retries) + 1):
        messages.append({"role": "user", "content": prompt})
        text = _call(call, messages)
        messages.append({"role": "assistant", "content": text})
        harvest = harvest_reflection(
            text,
            checkpoint_id=checkpoint_id,
            known_code_ids=known,
        )
        if harvest.result is not None:
            reflection = harvest.result
            return (
                (reflection, text, pinned_status) if return_text else reflection
            )
        partial = harvest.partial
        if (
            full
            and partial is not None
            and partial.has_block
            and partial.status
            and pinned_status is None
        ):
            # Preserve the pre-re-prompt verdict; a format re-emission must
            # not be allowed to flip it.
            pinned_status = partial.status
        if attempt >= format_retries:
            raise RuntimeError(
                f"AdaMAST reflection remained invalid: {harvest.error}"
            )
        if partial is not None and partial.has_block:
            prompt = render_format_repair(
                checkpoint_id=checkpoint_id,
                issues=partial.issues,
                full=full,
            )
        else:
            prompt = (
                f"AdaMAST reflection was invalid: {harvest.error}. Re-emit the "
                f"complete reflection for Checkpoint ID {checkpoint_id} in "
                "the exact required shape."
            )
    raise AssertionError("unreachable")


def _record(
    config: SingleLLMConfig,
    run_id: str,
    taxonomy_id: str,
    reflection: ReflectionResult,
    *,
    gate: str,
) -> None:
    record_reflection(
        Path(config.trace_output),
        {
            "taxonomy_id": taxonomy_id,
            "session_id": run_id,
        },
        reflection,
        gate=gate,
        task_id=run_id,
    )


def _call(call: MessageCall, messages: list[dict[str, str]]) -> str:
    result = call([dict(message) for message in messages])
    if not isinstance(result, str) or not result.strip():
        raise RuntimeError("single-LLM model call returned no text")
    return result.strip()


def _render_messages(messages: list[dict[str, str]]) -> str:
    return "\n\n".join(
        f"[{message['role'].upper()}]\n{message['content']}"
        for message in messages
    )


def _render_recent_messages(
    messages: list[dict[str, str]],
    *,
    max_messages: int,
    max_chars: int,
) -> str:
    if not messages:
        return ""
    selected = messages[-max_messages:]
    # Preserve the original task prompt when the window would otherwise lose it.
    first_user = next((m for m in messages if m.get("role") == "user"), None)
    if first_user is not None and first_user not in selected:
        selected = [first_user, *selected]
    rendered = _render_messages(selected)
    if len(rendered) <= max_chars:
        return rendered
    if first_user is not None:
        task_prefix = _render_messages([first_user])
        if len(task_prefix) < max_chars:
            tail_budget = max_chars - len(task_prefix) - 24
            if tail_budget > 0:
                return task_prefix + "\n\n[...]\n" + rendered[-tail_budget:]
    return rendered[-max_chars:]
