"""Taxonomy data model: ``Code``, ``Taxonomy``, ``JudgeLog`` + spec rendering.

A taxonomy is an ordered, mutable set of A/B/C failure codes. Code ids are
identities, not positions: once assigned (at generation or registration) an id
is never rewritten and retired ids are never reused, so evidence records,
recorded checkpoints, and human notes keep joining correctly across
retire / add / split. Internal ``uid``s key refinement-streak bookkeeping.

- **A** codes: system / execution failures.
- **B** codes: role-specific failures (each belongs to one declared agent role).
- **C** codes: domain-reasoning / cross-role failures.

Each code carries a name, definition, ``when_to_use`` / ``when_not_to_use``
guidance, and a list of ``detection_heuristics``. ``render_code_spec`` formats
all of those for the judge prompt.

The model can load from JSON files, AdaMAST pipeline output dicts, or
adamast's flat ``{repo, domain, codes: [{id, name, description, category,
...}]}`` records so the reflection judge can run against either source.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

# Canonical minted id shape; foreign shapes (MAST-12, bare "7") are kept
# verbatim but never advance the per-category high-water mark.
_STABLE_ID = re.compile(r"^([A-Z])\.([1-9]\d*)$")


@dataclass
class Code:
    """A single failure-mode entry. ``uid`` keys internal bookkeeping."""

    uid: str
    category: str            # "A" (system) | "B" (role-specific) | "C" (domain)
    code: str                # stable id, e.g. "C.3" (assigned once, never rewritten)
    name: str
    definition: str
    severity: str = "major"
    when_to_use: str = ""
    when_not_to_use: str = ""
    detection_heuristics: list = field(default_factory=list)
    applies_to_role: str = ""  # B-codes only
    origin: str = "seed"     # seed | added | split
    parent_uid: Optional[str] = None

    def to_full(self) -> dict:
        d = {
            "code": self.code, "name": self.name, "definition": self.definition,
            "severity": self.severity, "when_to_use": self.when_to_use,
            "when_not_to_use": self.when_not_to_use,
            "detection_heuristics": self.detection_heuristics,
            "origin": self.origin,
        }
        if self.category == "B":
            d["applies_to_role"] = self.applies_to_role
        return d


def render_code_spec(c: Code, indent: int = 2) -> str:
    """Render a Code's *full* spec for the judge / refiner prompt."""
    pad = " " * indent
    sub = " " * (indent + 4)
    lines = [f"{pad}{c.code}: {c.name}"]
    if c.definition:
        lines.append(f"{sub}definition: {c.definition}")
    if c.when_to_use:
        lines.append(f"{sub}use when:   {c.when_to_use}")
    if c.when_not_to_use:
        lines.append(f"{sub}NOT when:   {c.when_not_to_use}")
    if c.detection_heuristics:
        lines.append(f"{sub}detection heuristics:")
        for h in c.detection_heuristics:
            lines.append(f"{sub}  - {h}")
    return "\n".join(lines)


class Taxonomy:
    """Ordered, mutable set of A/B/C failure codes with stable uids."""

    def __init__(self, codes: list[Code], metadata: Optional[dict] = None):
        self.codes = codes
        self.metadata = metadata or {}
        self.version = int(self.metadata.get("version_n", 1))
        self._uid_counter = len(codes)
        self._id_floor: dict[str, int] = {}
        floor = self.metadata.get("id_high_water")
        if isinstance(floor, Mapping):
            for cat, n in floor.items():
                try:
                    self._id_floor[str(cat)] = int(n)
                except (TypeError, ValueError):
                    continue
        self.renumber()

    # ── construction ──
    @classmethod
    def from_json(cls, path: str | Path) -> "Taxonomy":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        tax = cls.from_dict(data)
        tax.metadata.setdefault("seed_path", str(path))
        return tax

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Taxonomy":
        """Build from the AdaMAST pipeline output dict (annotation_layer + full_layer)."""
        full = data.get("full_layer", {})
        ann = data.get("annotation_layer", {})
        codes: list[Code] = []
        n = 0
        for cat, key in (("A", "category_a"), ("B", "category_b"), ("C", "category_c")):
            full_cat = full.get(key, {})
            if isinstance(full_cat, dict) and full_cat:
                items = list(full_cat.values())
            elif isinstance(full_cat, list) and full_cat:
                items = full_cat
            else:
                items = ann.get(key, [])
            items = sorted(items, key=lambda c: int((re.findall(r"\d+", c.get("code", "0")) or ["0"])[0]))
            for c in items:
                n += 1
                codes.append(Code(
                    uid=f"u{n}", category=cat, code=c.get("code", f"{cat}.?"),
                    name=c.get("name", ""), definition=c.get("definition", ""),
                    severity=c.get("severity", "major"),
                    when_to_use=c.get("when_to_use", ""),
                    when_not_to_use=c.get("when_not_to_use", ""),
                    detection_heuristics=list(c.get("detection_heuristics", []) or []),
                    applies_to_role=c.get("applies_to_role", ""),
                    origin="seed",
                ))
        roles = data.get("role_definitions", {})
        metadata: dict[str, Any] = {
            "version_n": 1,
            "role_definitions": roles,
        }
        source_meta = data.get("metadata")
        if isinstance(source_meta, Mapping) and isinstance(
            source_meta.get("id_high_water"), Mapping
        ):
            metadata["id_high_water"] = dict(source_meta["id_high_water"])
        domain_info = full.get("domain_info")
        if isinstance(domain_info, Mapping):
            domain = domain_info.get("domain")
            if isinstance(domain, Mapping) and isinstance(domain.get("name"), str):
                metadata["domain"] = domain["name"].strip()
        return cls(codes, metadata)

    @classmethod
    def from_flat(cls, flat: Mapping[str, Any]) -> "Taxonomy":
        """Build from adamast's flat ``{repo, domain, codes: [...]}`` schema.

        Each code is ``{id, name, description, category, [severity, applies_to_role]}``.
        Used to feed the reflection judge against an existing adamast taxonomy
        without round-tripping through the AdaMAST pipeline output format.

        Supplied ids are preserved verbatim (they are identities that evidence
        and checkpoints join on); entries without an id get a fresh one above
        the persisted ``id_high_water`` mark.
        """
        codes: list[Code] = []
        for n, entry in enumerate(flat.get("codes", []) or [], start=1):
            cat = str(entry.get("category", "A")).upper()[:1]
            if cat not in ("A", "B", "C"):
                cat = "A"
            codes.append(Code(
                uid=f"u{n}",
                category=cat,
                code=str(entry.get("id") or "").strip(),
                name=entry.get("name", ""),
                definition=entry.get("description", "") or entry.get("definition", ""),
                severity=entry.get("severity", "major"),
                applies_to_role=entry.get("applies_to_role", ""),
                detection_heuristics=list(entry.get("detection_heuristics", []) or []),
                origin="seed",
            ))
        meta: dict[str, Any] = {
            "version_n": 1,
            "repo": flat.get("repo", ""),
            "domain": flat.get("domain", ""),
        }
        high_water = flat.get("id_high_water")
        if isinstance(high_water, Mapping):
            meta["id_high_water"] = dict(high_water)
        return cls(codes, meta)

    # ── queries ──
    def by_uid(self, uid: str) -> Optional[Code]:
        return next((c for c in self.codes if c.uid == uid), None)

    def prompt_block(self) -> str:
        return "\n".join(render_code_spec(c, indent=2) for c in self.codes)

    def code_index(self) -> dict[str, Code]:
        return {c.code: c for c in self.codes}

    # ── mutations ──
    def _new_uid(self) -> str:
        self._uid_counter += 1
        return f"u{self._uid_counter}"

    def renumber(self) -> None:
        """Assign ids to codes that lack one; never rewrite an existing id.

        Ids are identities: evidence records, recorded checkpoints, and human
        notes all join on them, so a registered id must survive every later
        mutation. The per-category high-water mark (persisted as
        ``id_high_water`` metadata) only ratchets up, so retired ids are never
        minted again. New codes get the next ``{category}.{n}`` above the mark.
        """
        floor = dict(self._id_floor)
        taken: set[str] = set()
        pending: list[Code] = []
        for c in self.codes:
            code_id = str(c.code or "").strip()
            if not code_id or code_id == "?" or code_id.endswith(".?") or code_id in taken:
                pending.append(c)
                continue
            taken.add(code_id)
            match = _STABLE_ID.match(code_id)
            if match:
                cat, n = match.group(1), int(match.group(2))
                floor[cat] = max(floor.get(cat, 0), n)
        for c in pending:
            cat = str(c.category or "A").upper()[:1]
            if not cat.isalpha():
                cat = "A"
            n = floor.get(cat, 0) + 1
            while f"{cat}.{n}" in taken:
                n += 1
            floor[cat] = n
            c.code = f"{cat}.{n}"
            taken.add(c.code)
        self._id_floor = floor
        if floor:
            self.metadata["id_high_water"] = dict(sorted(floor.items()))

    def retire(self, uid: str) -> None:
        self.codes = [c for c in self.codes if c.uid != uid]
        # No recompaction: the retired id stays in the high-water floor and is
        # never minted again, so its recorded evidence stays its own.
        self.renumber()

    def edit(self, uid: str, **fields) -> None:
        c = self.by_uid(uid)
        if c:
            for k, v in fields.items():
                if hasattr(c, k) and v is not None:
                    setattr(c, k, v)

    def split(self, uid: str, children: list[dict]) -> list[str]:
        idx = next((i for i, c in enumerate(self.codes) if c.uid == uid), None)
        if idx is None:
            return []
        parent = self.codes[idx]
        if children:
            first = children[0]
            parent.name = first.get("name", parent.name)
            parent.definition = first.get("definition", parent.definition)
            parent.detection_heuristics = list(first.get("detection_heuristics", parent.detection_heuristics) or [])
            parent.origin = "split"
        new_uids: list[str] = []
        insert_at = idx + 1
        for child in children[1:]:
            uid_new = self._new_uid()
            new_uids.append(uid_new)
            self.codes.insert(insert_at, Code(
                uid=uid_new, category=parent.category, code="?",
                name=child.get("name", ""), definition=child.get("definition", ""),
                severity=child.get("severity", parent.severity),
                detection_heuristics=list(child.get("detection_heuristics", []) or []),
                origin="split", parent_uid=parent.uid,
            ))
            insert_at += 1
        self.renumber()
        return new_uids

    def add(self, category: str, code_dict: dict) -> str:
        uid = self._new_uid()
        new = Code(
            uid=uid, category=category, code="?",
            name=code_dict.get("name", ""), definition=code_dict.get("definition", ""),
            severity=code_dict.get("severity", "major"),
            detection_heuristics=list(code_dict.get("detection_heuristics", []) or []),
            origin="added",
        )
        last_cat = max((i for i, c in enumerate(self.codes) if c.category == category),
                       default=len(self.codes) - 1)
        self.codes.insert(last_cat + 1, new)
        self.renumber()
        return uid

    # ── accessors ──
    def axis_codes(self, axis: str) -> list[Code]:
        return [c for c in self.codes if c.category == axis]

    def b_codes_by_role(self) -> dict[str, list[Code]]:
        out: dict[str, list[Code]] = {}
        for c in self.codes:
            if c.category == "B":
                out.setdefault(c.applies_to_role or "unspecified", []).append(c)
        return out

    def roles(self) -> list[str]:
        return list((self.metadata.get("role_definitions") or {}).keys()) or list(self.b_codes_by_role().keys())

    # ── serialization ──
    def to_dict(self) -> dict:
        def ann_row(c: Code) -> dict:
            row = {"code": c.code, "name": c.name, "definition": c.definition, "severity": c.severity}
            if c.category == "B":
                row["applies_to_role"] = c.applies_to_role
            return row
        ann = {
            "category_a": [ann_row(c) for c in self.codes if c.category == "A"],
            "category_b": [ann_row(c) for c in self.codes if c.category == "B"],
            "category_c": [ann_row(c) for c in self.codes if c.category == "C"],
        }
        return {
            "metadata": {**self.metadata, "version_n": self.version,
                         "counts": {"category_a": sum(c.category == "A" for c in self.codes),
                                    "category_b": sum(c.category == "B" for c in self.codes),
                                    "category_c": sum(c.category == "C" for c in self.codes),
                                    "total": len(self.codes)}},
            "role_definitions": self.metadata.get("role_definitions", {}),
            "annotation_layer": ann,
            "full_layer": {
                "category_a": {c.code: c.to_full() for c in self.codes if c.category == "A"},
                "category_b": {c.code: c.to_full() for c in self.codes if c.category == "B"},
                "category_c": {c.code: c.to_full() for c in self.codes if c.category == "C"},
            },
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


class JudgeLog:
    """Thread-safe per-code trigger counts: lifetime + current window.

    Drives the refinement gate's retirement logic: a code that is never fired in
    its lifetime AND unused for N consecutive gates is a retirement candidate.
    """

    def __init__(self, live_path: "Optional[str | Path]" = None):
        self._lock = threading.Lock()
        self.lifetime: dict[str, int] = {}
        self.window: dict[str, int] = {}
        self.consecutive_unused: dict[str, int] = {}
        self.history: list[dict] = []
        self.live_path = Path(live_path) if live_path else None

    def record(self, uid: str) -> None:
        with self._lock:
            self.lifetime[uid] = self.lifetime.get(uid, 0) + 1
            self.window[uid] = self.window.get(uid, 0) + 1

    def note_reflection(self, payload: dict) -> None:
        with self._lock:
            self.history.append(payload)
            if self.live_path is not None:
                try:
                    with self.live_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
                except Exception:
                    pass

    def close_window(self, uids: Sequence[str]) -> dict[str, int]:
        with self._lock:
            snapshot = dict(self.window)
            for uid in uids:
                if self.window.get(uid, 0) > 0:
                    self.consecutive_unused[uid] = 0
                else:
                    self.consecutive_unused[uid] = self.consecutive_unused.get(uid, 0) + 1
            self.window = {}
            return snapshot

    def retirement_candidates(self, codes: Sequence[Code], min_consecutive: int = 2) -> list[str]:
        with self._lock:
            return [c.uid for c in codes
                    if self.lifetime.get(c.uid, 0) == 0
                    and self.consecutive_unused.get(c.uid, 0) >= min_consecutive]


class CostMeter:
    """Lightweight cost accumulator used by the reflection judge.

    Use ``add_extra(usd)`` to accumulate per-call costs. Reflection judge will
    pass response cost here when the LLM transport surfaces it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.extra = 0.0

    def add_extra(self, usd: float) -> None:
        with self._lock:
            try:
                self.extra += float(usd or 0.0)
            except (TypeError, ValueError):
                pass

    def total(self) -> float:
        with self._lock:
            return self.extra
