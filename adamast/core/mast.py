"""The built-in MAST floor taxonomy (Cemri et al., 2025).

MAST is a CONSTANT, not a store record: it lives inside the package, is not
written to taxonomies/, and never appears in store.list_all. Interactive host
selectors still offer it explicitly as the built-in starting taxonomy.
It is the floor that Taxonomy Finding resolves to when nothing is inherited
(none -> MAST).

Structurally it is a taxonomy record like any other (taxonomy_id, repo, domain,
codes), so downstream layers can treat it uniformly. Its 14 codes are the MAST
failure modes, grouped by category (Specification, Coordination, Verification).
"""

from __future__ import annotations

import json
from pathlib import Path

_MAST_PATH = Path(__file__).resolve().parent / "mast.json"

# Pure-data constant, loaded once at import.
MAST = json.loads(_MAST_PATH.read_text(encoding="utf-8"))

MAST_ID = MAST["taxonomy_id"]  # "mast"
