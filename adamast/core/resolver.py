"""Taxonomy Finding resolver.

Resolves WHICH taxonomy a run inherits. Returns a taxonomy_id string, or
the literal "none". It does NOT load full taxonomy content for use — that
is Render, a later step.

The CLI maps inheritance requests into one of three forms, modelled by two
sentinels:

  * ABSENT  (no inherited taxonomy)  -> "none"
  * <id>    (explicit taxonomy id)   -> that id, or error if missing
  * NO_ID   (interactive picker)     -> launch blocking web view

`resolve()` returns the Finding decision: an id, or the literal "none".
"""

from __future__ import annotations

from . import mast, store

NONE = "none"

# Sentinels distinct from any real taxonomy_id string.
ABSENT = object()   # no inherited taxonomy requested
NO_ID = object()    # interactive picker requested


def resolve(inherit, store_dir=store.DEFAULT_STORE_DIR, launcher=None) -> str:
    """Resolve the inherit request to a taxonomy_id or "none".

    Parameters
    ----------
    inherit:
        ABSENT, NO_ID, or an explicit taxonomy_id string.
    store_dir:
        Where the flat store lives.
    launcher:
        Callable(store_dir) -> taxonomy_id | "none". Used only for the
        NO_ID (interactive) form; injected so tests need no browser.

    Raises
    ------
    store.TaxonomyNotFound
        When an explicit id has no record (never a silent "none").
    """
    # Form 1: no inherited taxonomy -> start from 0.
    if inherit is ABSENT:
        return NONE

    # Form 3: interactive picker -> blocking web view.
    if inherit is NO_ID:
        if launcher is None:
            raise RuntimeError("interactive inheritance requires a web-view launcher")
        chosen = launcher(store_dir)
        return chosen if chosen else NONE

    # Form 2: --inherit <taxonomy_id> -> that id, validated.
    taxonomy_id = inherit
    # The built-in MAST floor is a constant, not a store record. An explicit
    # request for it is the same Finding decision as inheriting nothing:
    # "none", which Render resolves to MAST.
    if taxonomy_id == mast.MAST_ID:
        return NONE
    if not store.exists(taxonomy_id, store_dir):
        raise store.TaxonomyNotFound(taxonomy_id)
    return taxonomy_id
