"""Flat taxonomy store: one JSON file per taxonomy, keyed by taxonomy_id.

Layout::

    taxonomies/
        <taxonomy_id>.json   # exactly one record per taxonomy

A record carries: taxonomy_id, repo, domain, codes (failure modes).
`repo` and `domain` are recorded display-only fields; only taxonomy_id
identifies, looks up, or selects a record.

Two operations are supported:
  * list_all   -> the light triple (taxonomy_id, repo, domain) per record
  * fetch_by_id -> the full record (all codes and their fields)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT_ADAMAST_HOME = Path(
    os.environ.get("ADAMAST_HOME", Path.home() / ".adamast")
).expanduser()
DEFAULT_STORE_DIR = Path(
    os.environ.get("ADAMAST_STORE_DIR", DEFAULT_ADAMAST_HOME / "taxonomies")
).expanduser()

# The three header fields surfaced by list_all (and the web-view table).
HEADER_FIELDS = ("taxonomy_id", "repo", "domain")

# The one canonical code shape every stored record must follow. Optional
# decorations (e.g. severity, applies_to_role, example) are tolerated, but
# these four must each be present as a non-empty string.
CANONICAL_CODE_FIELDS = ("id", "name", "description", "category")


class TaxonomyNotFound(Exception):
    """Raised when a taxonomy_id has no record in the store."""

    def __init__(self, taxonomy_id: str):
        self.taxonomy_id = taxonomy_id
        super().__init__(f"no taxonomy with id {taxonomy_id!r} found in store")


class TaxonomyAlreadyExists(Exception):
    """Raised when registration would overwrite a taxonomy without permission."""


class InvalidTaxonomy(ValueError):
    """Raised when a record does not follow the flat taxonomy schema."""


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _record_path(taxonomy_id: str, store_dir) -> Path:
    return Path(store_dir) / f"{taxonomy_id}.json"


def exists(taxonomy_id: str, store_dir=DEFAULT_STORE_DIR) -> bool:
    """True iff a record file for `taxonomy_id` exists in the store."""
    return _record_path(taxonomy_id, store_dir).is_file()


def list_all(store_dir=DEFAULT_STORE_DIR) -> list[dict]:
    """Return [{taxonomy_id, repo, domain}, ...] for every record.

    Global across all repos — repo is just a column, not a partition.
    Sorted by taxonomy_id for stable display.
    """
    store_dir = Path(store_dir)
    records: list[dict] = []
    for path in sorted(store_dir.glob("*.json")):
        # A single corrupt or hand-edited file must not brick the whole
        # listing (which backs --list and the picker); skip what we can't read.
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        records.append({field: data.get(field) for field in HEADER_FIELDS})
    records.sort(key=lambda r: r["taxonomy_id"] or "")
    return records


def fetch_by_id(taxonomy_id: str, store_dir=DEFAULT_STORE_DIR) -> dict:
    """Return the full record for `taxonomy_id`, or raise TaxonomyNotFound."""
    path = _record_path(taxonomy_id, store_dir)
    if not path.is_file():
        raise TaxonomyNotFound(taxonomy_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise InvalidTaxonomy(f"stored taxonomy {taxonomy_id!r} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise InvalidTaxonomy(f"stored taxonomy {taxonomy_id!r} is not a JSON object")
    return data


def display_name(record: dict) -> str:
    """Return a human-facing name without changing the immutable store key."""
    explicit = str(record.get("display_name") or "").strip()
    if explicit:
        return explicit
    domain = str(record.get("domain") or "").strip()
    if domain:
        return domain
    repo = str(record.get("repo") or "").strip()
    if repo:
        return f"{repo} failure modes"
    return str(record.get("taxonomy_id") or "Stored taxonomy").strip()


def taxonomy_hosts(record: dict) -> frozenset[str]:
    """Return every interactive host asserted by a taxonomy record.

    Host-neutral imported taxonomies return an empty set. Conflicting source,
    provenance, or identifier signals intentionally return more than one host
    so a historically cross-contaminated record is not accepted by either
    interactive integration.
    """
    hosts: set[str] = set()
    source = record.get("source")
    if isinstance(source, dict):
        normalized = _normalize_host(source.get("host_id") or source.get("host"))
        if normalized:
            hosts.add(normalized)
    provenance = record.get("provenance")
    driver = (
        str(provenance.get("driver") or "").strip().casefold()
        if isinstance(provenance, dict)
        else ""
    )
    taxonomy_id = str(record.get("taxonomy_id") or "").strip().casefold()
    if driver.startswith("codex") or taxonomy_id.startswith("tax-codex"):
        hosts.add("codex")
    if driver.startswith("claude") or taxonomy_id.startswith("tax-claude"):
        hosts.add("claude_code")
    return frozenset(hosts)


def taxonomy_host(record: dict) -> str:
    """Classify a taxonomy as neutral, host-owned, or historically mixed."""
    hosts = taxonomy_hosts(record)
    if not hosts:
        return "neutral"
    if len(hosts) > 1:
        return "mixed"
    return next(iter(hosts))


def compatible_with_host(record: dict, host: str) -> bool:
    """Allow neutral imports and same-host lineages, but reject mixed records."""
    normalized = _normalize_host(host)
    return bool(normalized) and taxonomy_host(record) in {"neutral", normalized}


def _normalize_host(value: object) -> str:
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(
        " ", "_"
    )
    if normalized in {"codex", "openai_codex"}:
        return "codex"
    if normalized in {"claude", "claude_code"}:
        return "claude_code"
    return ""


def register(record: dict, store_dir=DEFAULT_STORE_DIR, *, replace: bool = False) -> Path:
    """Validate and atomically store one taxonomy by taxonomy_id.

    Existing ids are rejected unless ``replace=True`` is explicit. The built-in
    ``mast`` constant is reserved and can never become a picker/store record.
    """
    _validate_record(record)
    taxonomy_id = record["taxonomy_id"]
    if taxonomy_id == "mast":
        raise InvalidTaxonomy("'mast' is reserved for the built-in MAST constant")

    store_dir = Path(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    target = _record_path(taxonomy_id, store_dir)
    if target.exists() and not replace:
        raise TaxonomyAlreadyExists(
            f"taxonomy {taxonomy_id!r} already exists; pass replace=True explicitly"
        )

    # Unique temp name per writer so two processes registering the same id
    # do not share (and tear) one temp file before the atomic replace.
    temporary = store_dir / f".{taxonomy_id}.{os.getpid()}.json.tmp"
    payload = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(target)
    return target


def unregister(taxonomy_id: str, store_dir=DEFAULT_STORE_DIR) -> bool:
    """Remove one store record, used only to roll back an unactivated write."""
    path = _record_path(taxonomy_id, store_dir)
    if not path.exists():
        return False
    path.unlink()
    return True


def _validate_record(record: dict) -> None:
    if not isinstance(record, dict):
        raise InvalidTaxonomy("taxonomy record must be an object")
    required = {"taxonomy_id", "repo", "domain", "codes"}
    missing = required - set(record)
    if missing:
        raise InvalidTaxonomy(f"missing required field(s): {sorted(missing)}")

    taxonomy_id = record["taxonomy_id"]
    if not isinstance(taxonomy_id, str) or not _SAFE_ID.fullmatch(taxonomy_id):
        raise InvalidTaxonomy(
            "taxonomy_id must be filesystem-safe: letters, numbers, '.', '_' or '-'"
        )
    for field in ("repo", "domain"):
        if not isinstance(record[field], str):
            raise InvalidTaxonomy(f"{field} must be a string")
    for field in ("display_name", "summary"):
        if field in record and (
            not isinstance(record[field], str) or not record[field].strip()
        ):
            raise InvalidTaxonomy(f"{field} must be a non-empty string")
    if not isinstance(record["codes"], list) or not record["codes"]:
        raise InvalidTaxonomy("codes must be a non-empty list")
    for index, code in enumerate(record["codes"]):
        if not isinstance(code, dict):
            raise InvalidTaxonomy(f"code at index {index} must be an object")
        label = (
            code["id"]
            if isinstance(code.get("id"), str) and code["id"].strip()
            else f"index {index}"
        )
        for cfield in CANONICAL_CODE_FIELDS:
            value = code.get(cfield)
            if not isinstance(value, str) or not value.strip():
                raise InvalidTaxonomy(
                    f"code {label!r} field {cfield!r} must be a non-empty string"
                )
