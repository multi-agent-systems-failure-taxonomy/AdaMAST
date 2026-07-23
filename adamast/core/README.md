# adamast/core/

The data model and session machinery everything else builds on: taxonomies,
traces, evidence, the taxonomy store, and the start/record/finish lifecycle.
No host or provider code lives here.

## Programs

| File | Purpose |
|---|---|
| [`lifecycle.py`](lifecycle.py) | `start_session` / `record_trace` / `pre_submission` / `end_session`: the agent-agnostic runtime lifecycle |
| [`taxonomy_data.py`](taxonomy_data.py) | The `Taxonomy` model: codes, categories, id renumbering |
| [`store.py`](store.py) | Flat on-disk taxonomy store: fetch, list, display names |
| [`mast.py`](mast.py) / [`mast.json`](mast.json) | The built-in 14-code MAST floor taxonomy (a constant, never a store record) |
| [`resolver.py`](resolver.py) | Taxonomy Finding: resolves an inherit request to a taxonomy id or the floor |
| [`lineage.py`](lineage.py) | Successor links between taxonomy versions; branch-head resolution |
| [`program.py`](program.py) | `ProgramWorkspace`: one learning stream's manifest, binding, and branches |
| [`traces.py`](traces.py) | `GenerationTrace` and `TraceStore`: one completed task = one trace |
| [`trace_formats.py`](trace_formats.py) | Normalizing external trace files into the AdaMAST shape |
| [`evidence.py`](evidence.py) | Recording checkpoint reflections and per-code runtime evidence |
| [`evidence_export.py`](evidence_export.py) | Exporting recorded evidence for analysis |
| [`reflection.py`](reflection.py) | Parsing Observe/Correlate/Map/Decide reflections |
| [`redaction.py`](redaction.py) | Automatic secret redaction before traces are persisted |
| [`config.py`](config.py) | `adamast.json` loading, field validation, path normalization |
| [`options.py`](options.py) | Shared option plumbing for CLIs |
| [`project_scope.py`](project_scope.py) / [`repository.py`](repository.py) | Git project identity and scoping |
| [`finding_cli.py`](finding_cli.py) | `adamast find`: list or select stored taxonomies |
| [`traces_cli.py`](traces_cli.py) | `adamast traces`: inspect trace state |
| [`fsio.py`](fsio.py) | Retry-safe file reads/writes shared across the runtime |
| [`worker_state.py`](worker_state.py) | Durable state for background workers |

The packaged config schema lives in [`assets/`](assets/).
