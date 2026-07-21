# Runtime API and custom harnesses

AdaMAST is designed so harnesses can integrate without reimplementing taxonomy finding, trace persistence, or learning thresholds.

## Runtime contract

A harness should:

1. start an AdaMAST session when a task starts;
2. pass a mandatory trace output or config containing `trace_output`;
3. let Finding resolve the active taxonomy;
4. invoke checkpoint or advisory gates at meaningful boundaries;
5. invoke the final gate before completion;
6. record one canonical trace at session end.

## Taxonomy selection contract

Finding returns:

- a concrete `taxonomy_id` when `--inherit <taxonomy_id>` is supplied;
- `none` when there is no inherited taxonomy or the interactive picker chooses start-from-zero.

The runtime maps `none` to built-in MAST. Finding itself does not load MAST as a store record.

## Public commands for harnesses

| Command | Use |
|---|---|
| `adamast-find` | Taxonomy selection and interactive picker. |
| `adamast-dashboard` | Dashboard process and local Web API. |
| `adamast-traces` | Trace status and inspection. |
| `adamast-register-taxonomy` | Store a completed taxonomy record. |
| `adamast-import-traces` | Build a taxonomy from existing trace files. |
| `adamast-doctor` | Validate paths, config, and optional dependencies. |
| `adamast-status` | Inspect one program's active taxonomy, pending traces, learning state, usage totals, last errors, and recent decisions. |

## What should stay harness-specific

Each harness owns:

- how it represents events;
- how it extracts tool/subagent output;
- which boundaries are meaningful enough to call AdaMAST;
- how it displays blocking messages to the agent.

AdaMAST owns:

- taxonomy selection;
- final-gate protocol validation;
- trace persistence;
- dashboard state;
- generation/refinement trigger timing;
- taxonomy storage.

See [INTEGRATION.md](INTEGRATION.md) for a broader pipeline integration guide.
See [WEB_API.md](WEB_API.md) for the localhost dashboard API response shapes.
