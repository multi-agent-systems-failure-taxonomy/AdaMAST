"""Shared AdaMAST reflection parsing and result types."""

from __future__ import annotations

import re
from dataclasses import dataclass

from adamast.protocol.gate import extract_statuses


@dataclass(frozen=True)
class CodeAssignment:
    code_id: str
    evidence: str


@dataclass(frozen=True)
class ReflectionResult:
    checkpoint_id: str
    observe: str
    assignments: tuple[CodeAssignment, ...]
    considered_codes: tuple[str, ...]
    none_apply: bool
    correlate: str
    decide: str


_BLOCK_MARKER = re.compile(r"(?im)^[ \t]*#*[ \t]*\**[ \t]*AdaMAST\s+reflection\b")
_SECTION = re.compile(
    r"(?ims)^[ \t]*[#>*\-]*[ \t]*\**[ \t]*"
    r"(Observe|Observation|Review|Map|Mapping|"
    r"Correlate|Root[ \t]*causes?|Causal|Decide|Decision|Action)"
    r"[ \t]*:?[ \t]*\**[ \t]*(.*?)"
    r"(?=^[ \t]*[#>*\-]*[ \t]*\**[ \t]*"
    r"(?:Observe|Observation|Review|Map|Mapping|"
    r"Correlate|Root[ \t]*causes?|Causal|Decide|Decision|Action)\b|\Z)"
)
_CHECKPOINT = re.compile(
    r"(?im)^\s*(?:[#>*-]\s*)*\**\s*Checkpoint\s+ID\s*:\s*([^\s*]+)"
)
_EVIDENCE = re.compile(
    r"(?i)\bevidence\s*(?:[:=\-–—]|\bis\b)\s*"
    r"(?:\"([^\"]+)\"|'([^']+)'|(.+))"
)
_DECIDE_CHANGE = re.compile(r"\bchange\s*:", re.I)
_DECIDE_NO_CHANGE = re.compile(r"\bno\s+change\s+needed\b", re.I)

REQUIRED_SECTIONS = ("observe", "map", "correlate", "decide")


def _canon_section(name: str) -> str:
    normalized = " ".join(name.strip().lower().split())
    if normalized in {"observation", "review"}:
        return "observe"
    if normalized == "mapping":
        return "map"
    if normalized.startswith("root cause") or normalized == "causal":
        return "correlate"
    if normalized in {"decision", "action"}:
        return "decide"
    return normalized


def _find_block(text: str) -> str | None:
    starts = [match.start() for match in _BLOCK_MARKER.finditer(text)]
    if not starts:
        return None
    return text[starts[-1]:]


def parse_reflection(
    text: str,
    *,
    checkpoint_id: str,
    known_code_ids: list[str] | tuple[str, ...],
) -> ReflectionResult:
    """Validate one Observe/Map/Correlate/Decide block.

    A valid Map either names at least one known code with evidence, or
    explicitly says none apply while naming at least one considered known code
    and giving evidence. The checker validates shape, not insight quality.
    """
    block = _find_block(text or "")
    if block is None:
        raise ValueError("missing `AdaMAST reflection` block")
    marker = _CHECKPOINT.search(block)
    if not marker or marker.group(1).strip() != checkpoint_id:
        raise ValueError(
            f"reflection must include `Checkpoint ID: {checkpoint_id}`"
        )
    return _parse_block(
        block, checkpoint_id=checkpoint_id, known_code_ids=known_code_ids
    )


def _parse_block(
    block: str,
    *,
    checkpoint_id: str,
    known_code_ids: list[str] | tuple[str, ...],
) -> ReflectionResult:
    """Validate a reflection block's content, trusting ``checkpoint_id``."""
    sections = {
        _canon_section(match.group(1)): match.group(2).strip()
        for match in _SECTION.finditer(block)
    }
    for name in REQUIRED_SECTIONS:
        if not sections.get(name):
            raise ValueError(f"reflection has an empty or missing {name.title()} step")

    known = tuple(str(code_id) for code_id in known_code_ids)
    map_text = sections["map"]
    negated_or_clean = re.compile(
        r"none\s+appl|\bconsidered\b|not[\s-]+(?:exhibit|fire|appl)|"
        r"does\s+not\s+apply|doesn'?t\s+apply|\bn/?a\b|no\s+failure|"
        r"\bclean\b|not\s+present|\babsent\b",
        re.I,
    )
    mentioned = _mentioned_codes(map_text, known)
    assignments: list[CodeAssignment] = []
    seen: set[str] = set()
    for line in map_text.splitlines():
        if not line.strip():
            continue
        evidence_match = _EVIDENCE.search(line)
        prefix = line[: evidence_match.start()] if evidence_match else line
        if negated_or_clean.search(prefix):
            continue
        evidence = _evidence(line)
        if not evidence:
            continue
        for code_id in known:
            if code_id in seen:
                continue
            if re.search(
                rf"(?<![A-Za-z0-9_.-]){re.escape(code_id)}"
                rf"(?![A-Za-z0-9_.-])",
                line,
                re.I,
            ):
                assignments.append(CodeAssignment(code_id, evidence))
                seen.add(code_id)

    none_apply = (not assignments) and bool(
        re.search(r"\bnone\s+appl(?:y|ies)\b", map_text, re.I)
    )
    if none_apply and not mentioned:
        mentioned = _mentioned_codes(block, known)
    if not mentioned:
        raise ValueError("Map must name at least one active taxonomy code")
    if none_apply and not _evidence(map_text):
        raise ValueError("`none apply` must include evidence or a reason")
    if not assignments and not none_apply:
        raise ValueError(
            "Map must fire a code (id + evidence) or explicitly say none apply"
        )

    decide = sections["decide"]
    return ReflectionResult(
        checkpoint_id=checkpoint_id,
        observe=sections["observe"],
        assignments=tuple(assignments),
        considered_codes=tuple(str(item) for item in mentioned),
        none_apply=none_apply,
        correlate=sections["correlate"],
        decide=decide,
    )


@dataclass(frozen=True)
class PartialReflection:
    """Best-effort content recovered from a malformed reflection.

    A reflection has form (parseable shape) and content (verdict, code
    assignments, evidence). When the form fails, the content that was still
    recoverable is captured here so recovery never has to re-sample it.
    """

    has_block: bool
    found_checkpoint_id: str | None
    present_sections: tuple[str, ...]
    missing_sections: tuple[str, ...]
    mentioned_codes: tuple[str, ...]
    statuses: tuple[str, ...]
    decide_change: bool | None
    issues: tuple[str, ...]

    @property
    def status(self) -> str | None:
        """The recovered final-gate status, only when it is unambiguous."""
        unique = set(self.statuses)
        if len(unique) == 1:
            return next(iter(unique))
        return None


@dataclass(frozen=True)
class HarvestedReflection:
    """Outcome of a lenient reflection parse.

    ``result`` is set when the block validated, possibly after substituting
    the hook-owned checkpoint id (``id_corrected``); the hook gains no
    information by making the model echo an id it already knows. Otherwise
    ``partial`` carries the recoverable content and ``error`` the strict
    parser's complaint.
    """

    result: ReflectionResult | None
    id_corrected: bool
    found_checkpoint_id: str | None
    partial: PartialReflection | None
    error: str | None


def harvest_reflection(
    text: str,
    *,
    checkpoint_id: str,
    known_code_ids: list[str] | tuple[str, ...],
) -> HarvestedReflection:
    """Parse leniently: strict result if possible, partial content otherwise."""
    block = _find_block(text or "")
    if block is None:
        error = "missing `AdaMAST reflection` block"
        partial = PartialReflection(
            has_block=False,
            found_checkpoint_id=None,
            present_sections=(),
            missing_sections=REQUIRED_SECTIONS,
            mentioned_codes=(),
            statuses=(),
            decide_change=None,
            issues=(error,),
        )
        return HarvestedReflection(None, False, None, partial, error)

    marker = _CHECKPOINT.search(block)
    found_id = marker.group(1).strip() if marker else None
    content_error: str | None = None
    result: ReflectionResult | None = None
    try:
        result = _parse_block(
            block, checkpoint_id=checkpoint_id, known_code_ids=known_code_ids
        )
    except ValueError as exc:
        content_error = str(exc)

    if result is not None:
        return HarvestedReflection(
            result, found_id != checkpoint_id, found_id, None, None
        )

    known = tuple(str(code_id) for code_id in known_code_ids)
    sections = {
        _canon_section(match.group(1)): match.group(2).strip()
        for match in _SECTION.finditer(block)
    }
    present = tuple(name for name in REQUIRED_SECTIONS if sections.get(name))
    missing = tuple(name for name in REQUIRED_SECTIONS if not sections.get(name))
    decide_text = sections.get("decide", "")
    if _DECIDE_CHANGE.search(decide_text):
        decide_change: bool | None = True
    elif _DECIDE_NO_CHANGE.search(decide_text):
        decide_change = False
    else:
        decide_change = None

    issues: list[str] = []
    if found_id != checkpoint_id:
        issues.append(f"missing or wrong `Checkpoint ID: {checkpoint_id}` line")
    for name in missing:
        issues.append(f"empty or missing {name.title()} step")
    if not missing and content_error:
        issues.append(content_error)

    partial = PartialReflection(
        has_block=True,
        found_checkpoint_id=found_id,
        present_sections=present,
        missing_sections=missing,
        mentioned_codes=_mentioned_codes(block, known),
        statuses=tuple(extract_statuses(block)),
        decide_change=decide_change,
        issues=tuple(issues),
    )
    return HarvestedReflection(None, False, found_id, partial, content_error)


def _evidence(text: str) -> str:
    match = _EVIDENCE.search(text)
    if not match:
        because = re.search(r"(?i)\bbecause\b\s*(.+)", text)
        return because.group(1).strip() if because else ""
    return next(
        (group.strip() for group in match.groups() if group and group.strip()),
        "",
    )


def mentioned_codes(
    text: str, known: tuple[str, ...] | list[str]
) -> tuple[str, ...]:
    """Return the known code ids cited in ``text``, in ``known`` order.

    Matching is case-insensitive and boundary-guarded, so an id never
    matches inside a longer id (``A.1`` does not match inside ``A.10``).
    The id shape is irrelevant: ``MAST-12``, ``A.1``, and bare ``1`` all
    match by exact escaped comparison rather than a token grammar. This is
    the single citation matcher shared by the reflection parser and the
    compact checkpoint transports; keep any future matching change here.
    """
    return tuple(
        code_id
        for code_id in known
        if re.search(
            rf"(?<![A-Za-z0-9_.-]){re.escape(code_id)}"
            rf"(?![A-Za-z0-9_.-])",
            text,
            re.IGNORECASE,
        )
    )


# Pre-public-name alias for internal callers.
_mentioned_codes = mentioned_codes
