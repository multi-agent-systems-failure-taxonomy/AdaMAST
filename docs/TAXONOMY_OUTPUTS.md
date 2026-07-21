# Taxonomy outputs and field guide

Every BASELINE run produces a self-contained result directory. Keep the whole
directory when reproducibility matters; `taxonomy.json` alone is enough for the
core judge.

## Output directory

```text
taxonomy-run/
├── taxonomy.json
├── taxonomy.html
├── taxonomy.draft.json
├── manifest.json
└── artifacts/
    ├── inputs/
    │   ├── traces.normalized.jsonl
    │   └── trace_report.json
    ├── draft/
    └── agreement/
```

## `taxonomy.json`

This is the stable, integration-neutral taxonomy document. The important fields
are the public status and the flat code catalog:

```json
{
  "schema_version": 1,
  "status": "accepted",
  "display_name": "Task scheduling failure taxonomy",
  "codes": [
    {
      "id": "A.1",
      "name": "Tool response truncated",
      "description": "...",
      "category": "A",
      "when_to_use": "...",
      "when_not_to_use": "...",
      "severity": "major"
    }
  ]
}
```

Consumers should use `codes[].id` as the displayed code, validate the taxonomy
status, and preserve unknown fields for forward compatibility.

## `manifest.json`

The manifest records how the result was produced:

- input paths and normalized trace report;
- provider, model, and output-token limit;
- agreement thresholds and final metrics;
- final status;
- paths to the draft and intermediate artifacts.

Credentials are never written to the manifest.

## Draft and agreement artifacts

`taxonomy.draft.json` is the pre-agreement layered A/B/C draft. The
`artifacts/draft/` directory contains generation-stage intermediates, while
`artifacts/agreement/` contains round-level annotations, reconciliations,
assignments, metrics, and refinements.

These files are research and debugging artifacts. Downstream integrations
should depend on `taxonomy.json`, not an internal round filename.

## Open the browser field guide

```bash
adamast view ./taxonomy-run/taxonomy.json
```

This creates or refreshes a self-contained `taxonomy.html` and opens it in the
default browser. The view is read-only and is scoped to that one taxonomy. It
does not start the adaptive runtime or expose the local runtime dashboard.

### Supply a manifest explicitly

When the manifest is not next to the taxonomy:

```bash
adamast view ./taxonomy.json --manifest ./run-manifest.json
```

### Write without opening

```bash
adamast view \
  ./taxonomy-run/taxonomy.json \
  --output ./exports/taxonomy-field-guide.html \
  --no-open
```

The resulting HTML is portable and can be archived with experiment artifacts.

## Open directly after generation

```bash
adamast generate \
  --provider openai \
  --traces ./traces.jsonl \
  --output ./taxonomy-run \
  --view
```

Generation always writes `taxonomy.html`; `--view` controls whether the browser
opens automatically.

## Preserve reproducibility

- Archive the normalized inputs, manifest, and agreement artifacts together.
- Record the exact model ID rather than relying only on a changing provider
  default.
- Keep taxonomy status and thresholds with any reported evaluation result.
- Do not edit `taxonomy.json` without recording that it is now a manually
  revised artifact.
