# Generate a taxonomy

BASELINE is the simplest AdaMAST workflow: provide completed traces, select a
model provider, and receive a standalone failure taxonomy with the full
inter-annotator agreement layer. It does not install AdaMAST into an agent
harness or refine the result as new traces arrive.

## Before you run

1. Install AdaMAST from the [documentation home](index.md#install-adamast).
2. Prepare a supported JSON or JSONL source.
3. Run `adamast validate` and inspect the normalized form when importing a new
   trace format.
4. Configure one provider as described in
   [Providers and models](PROVIDERS.md).

## Generate from the CLI

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

Add `--view` to open the field guide after generation:

```bash
adamast generate \
  --provider openai \
  --traces ./traces.jsonl \
  --output ./taxonomy-run \
  --view
```

## Generate from Python

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

## What BASELINE does

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

BASELINE never changes `review_required` to `accepted` silently.

## Configure the gate

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
after the stopping conditions are stable. Raising a target makes acceptance
stricter; it does not automatically make the taxonomy better.

## Interpret the exit code

| Exit code | Meaning |
| --- | --- |
| `0` | Generation completed and the taxonomy was accepted |
| `3` | Generation completed but the taxonomy requires review |
| `2` | Input, provider configuration, or pipeline execution failed |

Automation should inspect both the process exit code and `taxonomy.json.status`.
Do not feed a `review_required` taxonomy into production judging unless the
caller explicitly accepts that risk.

## Next steps

- Read [Outputs and field guide](TAXONOMY_OUTPUTS.md) to inspect the result.
- Read [Judge traces](JUDGING.md) to apply an accepted taxonomy to new traces.
- Move to [Adaptive runtime](GETTING_STARTED.md) only when taxonomy generation
  or refinement must happen as traces accumulate.
