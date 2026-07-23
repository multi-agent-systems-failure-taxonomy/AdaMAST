# Generate a taxonomy

On this page you turn a folder of completed traces into a validated failure
taxonomy — one command, no agent integration required.

BASELINE is the simplest AdaMAST workflow: provide completed traces, select a
model provider, and receive a standalone failure taxonomy with the full
inter-annotator agreement layer (independent annotators must be able to agree
on the codes before the result counts). It does not install AdaMAST into an
agent harness or refine the result as new traces arrive.

## 🚀 Generate from the CLI

1. Install AdaMAST from the [documentation home](index.md#install-adamast).
2. Prepare a supported JSON or JSONL source — see
   [Prepare traces](TRACE_FORMATS.md).
3. Run `adamast validate` and inspect the normalized form when importing a new
   trace format.
4. Configure one provider as described in
   [Providers and models](PROVIDERS.md).
5. Run:

```bash
adamast generate \
  --provider openai \
  --model gpt-5-nano \
  --traces ./traces.jsonl \
  --output ./taxonomy-run
```

The `--traces` value may be one accepted file or a directory. The output must
be a directory because AdaMAST writes the public taxonomy, a manifest, a
browser view, normalized inputs, and intermediate agreement artifacts.

!!! tip "Make it yours"
    | I want to… | Do this |
    | --- | --- |
    | Open the browser field guide after generation | add `--view` (details in [Outputs and field guide](TAXONOMY_OUTPUTS.md)) |
    | Tune the acceptance gate | `--max-rounds`, `--kappa-target`, `--coverage-floor` (see "Configure the gate") |
    | Call it from code instead | `generate_taxonomy(...)` (next section) |

## 🐍 Generate from Python

```python
from adamast import generate_taxonomy

taxonomy = generate_taxonomy(
    "./traces.jsonl",
    "./taxonomy-run",
    provider="openai",
    model="gpt-5-nano",
    open_viewer=True,
)

print(taxonomy["status"])
print(len(taxonomy["codes"]))
```

The function returns the same dictionary written to `taxonomy.json`.

## 🔬 What BASELINE does

### 1. Normalize traces

Every accepted source is converted to canonical AdaMAST JSONL and recorded with
a trace report. This keeps provider prompts independent from the original
benchmark or harness format.

### 2. Draft the taxonomy

The draft engine analyzes the domain, agent roles, and observable failure
patterns. It produces three layers of failure codes:

| Category | Meaning |
| --- | --- |
| **A** | System or execution failures that can affect any role |
| **B** | Role-specific quality failures |
| **C** | Domain reasoning or cross-role failures |

### 3. Run agreement refinement

Four independent annotators apply the draft to sampled traces. AdaMAST
reconciles discovered errors, deliberates over disagreements, rewrites weak
definitions, and measures both agreement and coverage. See the
[Agreement gate](AGREEMENT_GATE.md) for the full decision rule.

### 4. Publish with an explicit status

The public taxonomy receives one of two statuses:

- `accepted` when macro Fleiss kappa and error coverage both meet their targets;
- `review_required` when artifacts were produced but the configured gate was
  not satisfied.

!!! note
    BASELINE never changes `review_required` to `accepted` silently.

## 🎛️ Configure the gate

```bash
adamast generate \
  --provider anthropic \
  --model YOUR_MODEL_ID \
  --traces ./traces.jsonl \
  --output ./taxonomy-run \
  --max-rounds 5 \
  --kappa-target 0.75 \
  --coverage-floor 0.70
```

Use `--no-early-stop` when an experiment requires every configured round even
after the stopping conditions are stable.

!!! warning
    Raising a target makes acceptance stricter; it does not automatically make
    the taxonomy better.

## 🚥 Interpret the exit code

| Exit code | Meaning |
| --- | --- |
| `0` | Generation completed and the taxonomy was accepted |
| `3` | Generation completed but the taxonomy requires review |
| `2` | Input, provider configuration, or pipeline execution failed |

Automation should inspect both the process exit code and `taxonomy.json.status`.

!!! warning
    Do not feed a `review_required` taxonomy into production judging unless the
    caller explicitly accepts that risk.

When a fixed one-shot taxonomy stops being enough, move on to the
[adaptive runtime](GETTING_STARTED.md), which regenerates and refines as
traces accumulate.

## ➡️ Continue with

- [Outputs and field guide](TAXONOMY_OUTPUTS.md) — inspect what the run wrote.
- [Judge traces](JUDGING.md) — apply the accepted taxonomy to new traces.
