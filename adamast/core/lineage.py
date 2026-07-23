"""Immutable taxonomy-version lineage with branch-aware one-to-many edges."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .fsio import read_text_retry, write_text_atomic_retry

STATE_DIR = "_state"
SUCCESSORS_FILE = "successors.json"
LINEAGE_VERSION = 2


class TaxonomyLineage:
    def __init__(self, store_dir: Path | str) -> None:
        self.root = Path(store_dir) / STATE_DIR
        self.path = self.root / SUCCESSORS_FILE

    def load(self) -> dict[str, list[str]]:
        """Return parent -> children while accepting the legacy one-child file."""
        document = self._load_document()
        return {
            parent: [str(child) for child in children]
            for parent, children in document["children"].items()
        }

    def children(self, taxonomy_id: str) -> tuple[str, ...]:
        return tuple(self.load().get(str(taxonomy_id), ()))

    def resolve_latest(self, taxonomy_id: str) -> str:
        """Follow only an unambiguous legacy-style chain.

        Runtime branches must use their manifest head instead. This compatibility
        helper remains for callers that explicitly inspect a single-child chain.
        """
        links = self.load()
        current = str(taxonomy_id)
        seen: set[str] = set()
        while True:
            children = links.get(current, [])
            if not children:
                return current
            if len(children) > 1:
                raise ValueError(
                    f"taxonomy {current!r} has multiple successors; latest is "
                    "branch-relative"
                )
            if current in seen:
                raise ValueError(f"taxonomy successor cycle at {current!r}")
            seen.add(current)
            current = children[0]

    def add_successor(
        self,
        old_id: str,
        new_id: str,
        *,
        branch_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        old_id, new_id = str(old_id), str(new_id)
        if old_id == new_id:
            raise ValueError("a taxonomy cannot be its own successor")
        with self.locked() as document:
            children = document["children"].setdefault(old_id, [])
            if new_id not in children:
                children.append(new_id)
            edge = {
                "parent_taxonomy_id": old_id,
                "child_taxonomy_id": new_id,
                "branch_id": str(branch_id) if branch_id else None,
                "job_id": str(job_id) if job_id else None,
            }
            if not any(
                item.get("parent_taxonomy_id") == old_id
                and item.get("child_taxonomy_id") == new_id
                and item.get("branch_id") == edge["branch_id"]
                for item in document["edges"]
                if isinstance(item, dict)
            ):
                document["edges"].append(edge)

    def remove_successor(self, old_id: str, new_id: str) -> None:
        old_id, new_id = str(old_id), str(new_id)
        with self.locked() as document:
            children = document["children"].get(old_id, [])
            document["children"][old_id] = [
                child for child in children if child != new_id
            ]
            if not document["children"][old_id]:
                document["children"].pop(old_id, None)
            document["edges"] = [
                edge
                for edge in document["edges"]
                if not (
                    isinstance(edge, dict)
                    and edge.get("parent_taxonomy_id") == old_id
                    and edge.get("child_taxonomy_id") == new_id
                )
            ]

    def _load_document(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": LINEAGE_VERSION, "children": {}, "edges": []}
        raw = json.loads(read_text_retry(self.path))
        if isinstance(raw, dict) and raw.get("version") == LINEAGE_VERSION:
            children = raw.get("children")
            edges = raw.get("edges")
            if not isinstance(children, dict) or not isinstance(edges, list):
                raise ValueError("invalid taxonomy lineage document")
            normalized: dict[str, list[str]] = {}
            for parent, values in children.items():
                if not isinstance(values, list):
                    raise ValueError("taxonomy lineage children must be lists")
                normalized[str(parent)] = list(dict.fromkeys(map(str, values)))
            return {
                "version": LINEAGE_VERSION,
                "children": normalized,
                "edges": [dict(item) for item in edges if isinstance(item, dict)],
            }
        if not isinstance(raw, dict):
            raise ValueError("invalid legacy taxonomy lineage document")
        children = {str(parent): [str(child)] for parent, child in raw.items()}
        edges = [
            {
                "parent_taxonomy_id": parent,
                "child_taxonomy_id": values[0],
                "branch_id": None,
                "job_id": None,
            }
            for parent, values in children.items()
        ]
        return {"version": LINEAGE_VERSION, "children": children, "edges": edges}

    @contextmanager
    def locked(self, *, timeout: float = 5.0, stale_after: float = 60.0):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / ".lineage.lock"
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > stale_after:
                        lock.rmdir()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for lineage lock {lock}")
                time.sleep(0.05)
        document = self._load_document()
        try:
            yield document
            write_text_atomic_retry(
                self.path,
                json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            )
        finally:
            try:
                lock.rmdir()
            except FileNotFoundError:
                pass
