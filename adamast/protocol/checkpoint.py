"""The compact four-field AdaMAST checkpoint transport.

This is the single implementation of the private checkpoint protocol —
field parsing, citation matching against the active taxonomy, and the
next-action repair heuristic — shared by every host transport (Claude
Code and Codex previously carried byte-identical copies). Keep any
protocol change here.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from adamast.core.reflection import (
    CodeAssignment,
    ReflectionResult,
    mentioned_codes,
)


def new_checkpoint_id(gate: str) -> str:
    return f"adamast-{gate}-{uuid.uuid4().hex[:12]}"


def compact_reflection(
    text: str,
    state: dict[str, Any],
    *,
    gate: str,
) -> tuple[ReflectionResult | None, str, str | None]:
    """Validate one compact checkpoint against the session's taxonomy."""
    fields, error = compact_checkpoint_fields(text)
    if fields is None:
        return None, "MISSING_CHECKPOINT", error
    # Registered taxonomies store each code id under "id" (see
    # register_taxonomy._taxonomy_to_flat); "code_id" is accepted as a
    # legacy fallback. Order is preserved so assignments follow taxonomy
    # order deterministically (a set-valued lookup would make it
    # nondeterministic).
    known: list[str] = []
    for code in state.get("taxonomy", {}).get("codes", []):
        if not isinstance(code, dict):
            continue
        code_id = str(code.get("id") or code.get("code_id") or "").strip()
        if code_id and code_id not in known:
            known.append(code_id)
    codes_text = fields["relevant codes"]
    none_apply = bool(re.search(r"\b(?:none|none\s+apply|n/?a)\b", codes_text, re.I))
    # Exact escaped matching against the active taxonomy (shape-agnostic:
    # MAST-12, A.1, and bare numeric ids all match; A.1 never matches
    # inside A.10). Shared with the long-form reflection parser.
    mentioned = list(mentioned_codes(codes_text, known))
    if not none_apply and not mentioned:
        return (
            None,
            "MISSING_CHECKPOINT",
            "Relevant codes must name an active taxonomy code or `none apply`",
        )
    evidence = fields["evidence"]
    assignments = tuple(
        CodeAssignment(code_id=code_id, evidence=evidence) for code_id in mentioned
    )
    next_action = fields["next action"]
    status = (
        "REPAIR_REQUIRED"
        if next_action_requires_repair(next_action)
        else "READY_TO_SUBMIT"
    )
    return (
        ReflectionResult(
            checkpoint_id=new_checkpoint_id(gate),
            observe=fields["checkpoint"],
            assignments=assignments,
            considered_codes=tuple(mentioned),
            none_apply=not assignments,
            correlate=evidence,
            decide=next_action,
        ),
        status,
        None,
    )


def compact_checkpoint_fields(
    text: str,
) -> tuple[dict[str, str] | None, str | None]:
    """Parse the private four-field AdaMAST checkpoint transport."""
    lines = [checkpoint_line(line) for line in str(text or "").splitlines()]
    starts = [
        index
        for index, line in enumerate(lines)
        if re.match(r"(?i)^checkpoint\s*:", line)
        and not re.match(r"(?i)^checkpoint\s+id\s*:", line)
    ]
    if not starts:
        return None, "missing `Checkpoint:` line"
    fields: dict[str, str] = {}
    for line in lines[starts[-1] :]:
        match = re.match(
            r"(?i)^(checkpoint|relevant\s+codes|evidence|next\s+action)\s*:\s*(.*)$",
            line,
        )
        if match:
            fields[" ".join(match.group(1).lower().split())] = match.group(2).strip()
    for name in ("checkpoint", "relevant codes", "evidence", "next action"):
        if not fields.get(name):
            return None, f"missing or empty `{name.title()}:` line"
    return fields, None


def checkpoint_line(line: str) -> str:
    cleaned = re.sub(r"^[\s#>*-]+", "", str(line or "")).strip()
    return cleaned.replace("**", "").replace("__", "").strip()


def next_action_requires_repair(value: str) -> bool:
    """Classify explicit unresolved intent without treating negation as failure."""
    normalized = " ".join(str(value or "").casefold().split())
    if re.search(
        r"\b(?:no|not)\s+(?:further\s+)?(?:action|repair|change|work)\s+required\b",
        normalized,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:repair|fix|resolve|address|correct|rework|retry|blocked)\b"
            r"|\breport\s+unresolved\b"
            r"|\b(?:repair|verification|action)\s+(?:is\s+)?required\b",
            normalized,
        )
    )
