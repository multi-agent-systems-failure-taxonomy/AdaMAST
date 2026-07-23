# Taxonomies

This page shows how stored taxonomies are identified, reused across runs,
registered by hand, and related to each other over time.

Two rules drive everything below:

- A taxonomy is selected by one immutable key, `taxonomy_id`. It never
  changes for a stored taxonomy.
- `display_name`, `repo`, `domain`, and `summary` are user-facing metadata
  only. They never route, group, or select taxonomies, so changing
  `display_name` changes what people see without breaking stored traces or
  lineage references.

## 📄 Record shape

```json
{
  "taxonomy_id": "my-taxonomy-v1",
  "display_name": "Checkout Workflow Reliability",
  "repo": "display-only",
  "domain": "display-only",
  "summary": "Failure modes affecting checkout implementation and verification.",
  "codes": [
    {
      "id": "C-1",
      "name": "Observable failure name",
      "description": "Task-neutral diagnostic definition.",
      "category": "Custom"
    }
  ]
}
```

The store is flat: one JSON file per taxonomy, named `<taxonomy_id>.json`.

## 🧭 Built-in MAST

If a run starts without inheritance, Finding returns `none` and the runtime
resolves that to the built-in MAST constant.

!!! note
    MAST is not a store record and does not appear in `store.list_all`. Codex
    and Claude Code selectors still offer it explicitly as the built-in
    starting taxonomy. The standalone `adamast find --inherit-pick` command
    lists stored records rather than host-specific built-ins.

## 🎯 Inherit a taxonomy

To start a run from a taxonomy you already trust:

1. Browse what is stored with the interactive picker:

    ```bash
    adamast find --inherit-pick
    ```

    The searchable picker shows human-readable names, summaries, source
    projects, code counts, and immutable IDs as secondary metadata. Clicking
    a row opens the full taxonomy content.

2. Or pass the ID directly, non-interactively:

    ```bash
    adamast single-run --config adamast.json --inherit my-taxonomy-v1 --model gpt-5 --task "..."
    ```

!!! note "Opting out"
    Choosing **Start without a stored taxonomy** returns `none` in the
    standalone blocking CLI flow. In a Codex or Claude Code selector,
    **No taxonomy** disables AdaMAST only for that conversation.

## 📝 Register a taxonomy

To store a taxonomy file you prepared yourself:

```bash
adamast register-taxonomy --file taxonomy.json --id my-taxonomy-v1
```

## 📥 Import existing traces

Generate and store an inheritable taxonomy from traces you already have:

```bash
adamast import-traces \
  --config adamast.json \
  --traces ./traces
```

Imported taxonomies become normal flat store records after acceptance. If you
need a specific ID, register a prepared taxonomy with
`adamast register-taxonomy --id ...`.

## 🌳 Lineage

Generated and refined taxonomies get new taxonomy IDs. Refinement records a
parent-to-child edge from the exact version used by that conversation branch.
One parent may have several children when conversations evolve independently;
each branch manifest records its own head, so no child is treated as the global
latest taxonomy after a split.

## ➡️ Continue with

- [Single-LLM integration](SINGLE_LLM.md): the simplest runtime that inherits
  and learns taxonomies.
- [Native taxonomy learning](NATIVE_LEARNING.md): how Codex and Claude Code
  create new taxonomy versions.
