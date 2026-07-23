# Runtime API and custom harnesses

Learn the contract a custom harness follows: which calls it makes, which
commands it can rely on, and what stays on each side of the line. Harnesses
integrate without reimplementing taxonomy finding, trace persistence, or
learning thresholds.

## 📜 Runtime contract

A harness should:

1. start an AdaMAST session when a task starts;
2. pass a mandatory trace output or config containing `trace_output`;
3. let Finding resolve the active taxonomy;
4. invoke checkpoints (advisory or blocking gates) at meaningful boundaries;
5. invoke the final gate before completion;
6. record one canonical trace at session end.

## 🧭 Taxonomy selection contract

Finding returns:

| Input | Finding result |
|---|---|
| `--inherit <taxonomy_id>` is supplied | that concrete `taxonomy_id` |
| no inherited taxonomy, or the interactive picker chooses start-from-zero | `none` |

!!! note
    The runtime maps `none` to built-in MAST. Finding itself does not load
    MAST as a store record.

## 🧰 Public commands for harnesses

| Command | Use |
|---|---|
| `adamast find` | Taxonomy selection and interactive picker. |
| `adamast dashboard` | Dashboard process and local Web API. |
| `adamast traces` | Trace status and inspection. |
| `adamast register-taxonomy` | Store a completed taxonomy record. |
| `adamast import-traces` | Build a taxonomy from existing trace files. |
| `adamast doctor` | Validate paths, config, and optional dependencies. |
| `adamast status` | Inspect one program's active taxonomy, pending traces, learning state, usage totals, last errors, and recent decisions. |

## ⚖️ What should stay harness-specific

| Your harness owns | AdaMAST owns |
|---|---|
| How it represents events | Taxonomy selection |
| How it extracts tool/subagent output | Final-gate protocol validation |
| Which boundaries are meaningful enough to call AdaMAST | Trace persistence; dashboard state |
| How it displays blocking messages to the agent | Generation/refinement trigger timing; taxonomy storage |

## ➡️ Continue with

- [Custom agent harness](INTEGRATION.md): the broader pipeline integration
  guide.
- [Web API](WEB_API.md): the localhost dashboard API response shapes.
