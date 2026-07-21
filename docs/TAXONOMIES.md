# Taxonomies

AdaMAST taxonomies are selected by one immutable key: `taxonomy_id`.

`display_name`, `repo`, `domain`, and `summary` are user-facing metadata. They
do not route, group, or select taxonomies. Changing `display_name` changes what
people see without breaking stored traces or lineage references.

## Record shape

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

## Built-in MAST

If a run starts without inheritance, Finding returns `none` and the runtime resolves that to the built-in MAST constant.

MAST is not a store record and does not appear in the interactive picker.

## Inherit a taxonomy

Non-interactive:

```bash
adamast-single-run --config adamast.json --inherit my-taxonomy-v1 --model gpt-5 --task "..."
```

Interactive picker:

```bash
adamast-find --inherit-pick
```

The searchable picker shows human-readable names, coverage summaries, source
projects, code counts, and immutable IDs as secondary metadata.

Clicking a row opens the full taxonomy content. Choosing "use none / start from
0" returns `none` in the blocking CLI flow.

## Register a taxonomy

```bash
adamast-register-taxonomy --file taxonomy.json --id my-taxonomy-v1
```

## Import existing traces

Generate and store an inheritable taxonomy from traces you already have:

```bash
adamast-import-traces \
  --config adamast.json \
  --traces ./traces
```

Imported taxonomies become normal flat store records after acceptance. If you need a specific ID, register a prepared taxonomy with `adamast-register-taxonomy --id ...`.

## Lineage

Generated and refined taxonomies get new taxonomy IDs. Refinement records lineage from the previous taxonomy to the accepted replacement so future runs can preserve the evolution history.
