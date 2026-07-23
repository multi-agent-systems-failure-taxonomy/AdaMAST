# Judge traces

On this page you label completed traces with the failure codes their evidence
supports, using a taxonomy you already have, from the CLI or from Python.

The judge applies one existing taxonomy to one or more completed traces. A
trace can carry several failure modes at once (even a single failure point
can express more than one), so the default judge returns every supported
code, each with its evidence.

## 🎯 What it does

`adamast judge` selects every failure code the trace evidence supports
(zero, one, or several per trace), quoting the evidence for each. Finding
nothing wrong is an explicit answer (`none_apply`), not an error. Returned
codes are always validated against the taxonomy, and it reads the same trace
formats and providers as generation.

Pass `--mode single` when you need exactly one best-supported code per trace,
with a confidence score and a recovery hint.

Judges that map failure points or analyze coverage, calibration, and
causality live in [Choose a judge](JUDGE_TYPES.md).

## 🚀 Judge from the CLI

```bash
adamast judge \
  --provider openai \
  --model gpt-5-nano \
  --taxonomy ./taxonomy-run/taxonomy.json \
  --traces ./new-traces.jsonl \
  --output ./judgments.json
```

`--traces` accepts one file or a directory in any supported format. Omit
`--output` to print the JSON result to standard output.

!!! tip "Make it yours"
    | I want to… | Do this |
    | --- | --- |
    | Print to standard output instead of a file | omit `--output` |
    | Judge a whole directory of traces | point `--traces` at the directory |
    | Force exactly one code per trace | `--mode single` |
    | Give the model more of each long trace | `--max-trace-chars 12000` (see "Trace sampling limit") |
    | Evaluate a `review_required` taxonomy anyway | `--allow-review-required` (see below) |

## 🐍 Judge from Python

### Reuse one judge

Create one judge when evaluating multiple traces with the same taxonomy and
model:

```python
from adamast import create_judge, load_traces

traces = load_traces("./new-traces.jsonl")
judge = create_judge(
    "./taxonomy-run/taxonomy.json",
    provider="anthropic",
    model="YOUR_MODEL_ID",
)

for diagnosis in judge.judge_many(traces):
    print(diagnosis.trace_id, diagnosis.code_ids(), diagnosis.none_apply)
```

Pass `mode="single"` to `create_judge` for the one-code-per-trace judge; its
diagnoses carry `code`, `confidence`, and `recovery_hint` instead. In code,
the default judge is a `SelectionTraceJudge` returning `SelectionDiagnosis`
entries, and the single-code judge is a `TaxonomyJudge` returning
`Diagnosis` entries; all four names are importable from `adamast`.

### One-off helpers

`judge_trace(...)` judges one normalized trace dictionary.
`judge_traces(...)` accepts an iterable of normalized dictionaries or a file or
directory path. Both accept the same `mode` parameter.

```python
from adamast import judge_traces

diagnoses = judge_traces(
    "./taxonomy-run/taxonomy.json",
    "./new-traces.jsonl",
    provider="bedrock",
    model="YOUR_BEDROCK_MODEL_ID",
    aws_region="us-east-1",
)
```

## 📄 Diagnosis schema

Each trace produces a validated diagnosis. The default judge returns every
supported failure mode with its evidence:

```json
{
  "trace_id": "trace-17",
  "failure_modes": [
    {
      "code": "A.1",
      "name": "Tool response truncated",
      "evidence": "Specific evidence identified in the trace",
      "confidence": "high",
      "severity": "moderate"
    }
  ],
  "none_apply": false,
  "judge_metadata": {"judge": "selection", "warnings": []}
}
```

A clean trace returns `"failure_modes": []` with `"none_apply": true`.

With `--mode single`, each trace instead produces one best-supported code:

```json
{
  "trace_id": "trace-17",
  "code": "A.1",
  "label": "Tool response truncated",
  "category": "A",
  "evidence": "Specific evidence identified in the trace",
  "confidence": 0.86,
  "recovery_hint": "What to try differently"
}
```

Returned codes are validated against the loaded taxonomy in both modes.
AdaMAST does not replace an unknown code with a guessed closest match: the
default judge drops unknown codes and records a warning in
`judge_metadata`, and the single-code judge raises `JudgeResponseError`
(importable from `adamast`) on unknown codes, malformed JSON, or confidence
values outside `[0, 1]`.

## 🚦 Accepted taxonomy requirement

A generated taxonomy with `status: review_required` is rejected by default.
For an explicit experiment only:

```bash
adamast judge \
  --allow-review-required \
  --provider openai \
  --taxonomy ./taxonomy-run/taxonomy.json \
  --traces ./new-traces.jsonl
```

!!! warning
    The override permits evaluation; it does not change the taxonomy status or
    claim that the agreement gate passed.

## ✂️ Trace sampling limit

The judge allocates `6000` prompt characters to each normalized trace by
default, in both modes. Longer trajectories preserve the beginning and end with an explicit
truncation marker between them.

```bash
adamast judge \
  --max-trace-chars 12000 \
  --provider openai \
  --taxonomy ./taxonomy-run/taxonomy.json \
  --traces ./new-traces.jsonl
```

!!! note
    Increase this only when evidence regularly falls outside the retained
    windows; larger prompts increase cost and may not improve classification.

## 🧩 Taxonomy compatibility

The judge accepts the stable flat AdaMAST schema and legacy layered ATLAS
taxonomy files during migration. New integrations should use the flat
`taxonomy.json` generated by `adamast generate`.

## ➡️ Continue with

- [Choose a judge](JUDGE_TYPES.md): specialized judges for causality,
  coverage, quality, and calibration.
- [Adaptive runtime](GETTING_STARTED.md): checkpoints and learning during
  agent work.
