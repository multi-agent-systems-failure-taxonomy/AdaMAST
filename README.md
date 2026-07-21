# AdaMAST

[![Paper](https://img.shields.io/badge/paper-arXiv-B31B1B?logo=arxiv&logoColor=white)](https://arxiv.org/abs/2607.16387)
[![Docs](https://img.shields.io/badge/docs-website-2457D6)](https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs/)

**AdaMAST** generates a structured failure taxonomy from a set of agent traces — and only calls it done when four independent LLM annotators can actually agree on how to apply it. Its separate **JUDGES** layer can then apply that taxonomy to new traces without coupling it to a benchmark or agent harness.

Give it traces from any agent system, get back a JSON taxonomy with three categories:

| Category | What it captures |
| --- | --- |
| **A** — System failures | Things that can happen to *any* agent, regardless of role |
| **B** — Role-specific quality failures | One specific role doing its job poorly |
| **C** — Domain reasoning failures | Reasoning errors specific to the problem domain |

What makes AdaMAST different is the **agreement gate**: after drafting, four annotators independently apply the taxonomy back to the traces, disagreements are deliberated, low-agreement definitions are rewritten, and the result is only marked `accepted` when inter-annotator agreement (macro Fleiss κ) and error coverage both clear their thresholds. A taxonomy that fails the gate is delivered as `review_required` — never silently presented as good.

---

## Quickstart

**1. Install**

```bash
git clone https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST.git
cd AdaMAST
pip install -e ".[all]"          # or a single provider: ".[openai]", ".[bedrock]", ...
```

**2. Set a provider credential** (any one of these works)

```bash
export OPENAI_API_KEY=sk-...                 # OpenAI
export ANTHROPIC_API_KEY=...                 # Anthropic
export GEMINI_API_KEY=...                    # Google Gemini
export AWS_BEARER_TOKEN_BEDROCK=...          # AWS Bedrock (or the normal AWS chain)
```

**3. Generate a taxonomy from your traces**

CLI:

```bash
adamast generate --provider openai --model gpt-5-nano \
    --traces my_traces.jsonl --output ./my_tax
```

Python:

```python
from adamast import generate_taxonomy

taxonomy = generate_taxonomy(
    "my_traces.jsonl",
    "./my_tax",
    provider="openai",
    model="gpt-5-nano",
)
print(taxonomy["status"], len(taxonomy["codes"]))
```

The result is saved to `./my_tax/taxonomy.json` with a `manifest.json` recording the gate metrics and every intermediate artifact.

**4. View it**

```bash
adamast view ./my_tax/taxonomy.json
```

Opens a self-contained, read-only HTML field guide for the taxonomy. Add `--view` to the generate command to get it immediately.

**5. Judge new traces**

```bash
adamast judge --provider openai --model gpt-5-nano \
    --taxonomy ./my_tax/taxonomy.json \
    --traces new_traces.jsonl \
    --output judgments.json
```

This applies the accepted taxonomy to every trace in the file and writes a structured diagnosis for each one. JUDGES uses the same provider configuration and accepted trace formats as BASELINE.

That's it. Everything below is reference material.

---

## Trace formats

The loader auto-detects and normalizes several input shapes:

| Format | Detection | Example sources |
| --- | --- | --- |
| **AdaMAST native** | `raw_trajectory` field present | This library's canonical shape |
| **Messages** | `messages` list of role/content dicts | Chat-style transcripts |
| **tau-bench** | `traj` + `task_id` + `reward` keys | tau-bench evaluation outputs |
| **MAD envelope** | `mas_name` + `trace` keys | MAD / MAST-Data exports |
| **Codex CLI session** | `type: session_meta/response_item/...` entries | OpenAI Codex CLI logs |
| **Event log** | `event` field in JSONL entries | Custom agent event streams |

Files can be JSON (an object, an array, or `{"traces": [...]}`) or JSONL. Check what the loader sees before spending model calls:

```bash
adamast validate my_traces.jsonl      # report: count, formats, empty trajectories
adamast normalize my_traces.jsonl --output canonical.jsonl
```

File-based JUDGES input goes through this same loader. Python callers that pass trace dictionaries directly should use the normalized shape:

```json
{
  "problem_id": "trace-17",
  "task": "Optional original task",
  "raw_trajectory": "The agent/tool trajectory to diagnose",
  "metadata": {}
}
```

---

## What gets generated

```text
my_tax/
├── taxonomy.json          # the public taxonomy (flat codes list + status)
├── taxonomy.html          # read-only browser field guide
├── taxonomy.draft.json    # pre-agreement draft (layered A/B/C schema)
├── manifest.json          # gate metrics, thresholds, provider, artifact index
└── artifacts/
    ├── inputs/            # normalized traces + trace report
    ├── draft/             # draft engine intermediates
    └── agreement/         # per-round annotation and refinement artifacts
```

`taxonomy.json` is integration-neutral: a flat `codes` list (`id`, `name`, `description`, `category`, plus `when_to_use` / `when_not_to_use` / `severity` where available) with `status` either:

- **`accepted`** — κ and coverage both passed the gate; or
- **`review_required`** — artifacts were produced but the agreement gate failed.

---

## JUDGES: apply a taxonomy

JUDGES is intentionally separate from taxonomy generation and runtime integration. It takes an existing taxonomy plus one or more new traces and asks one configured model to select the single best-supported failure code.

Each diagnosis contains:

```json
{
  "trace_id": "trace-17",
  "code": "A.2",
  "label": "Tool response truncated",
  "category": "A",
  "evidence": "Specific evidence identified in the trace",
  "confidence": 0.86,
  "recovery_hint": "What to try differently"
}
```

The code, label, and category are validated against the loaded taxonomy. Unknown codes, invalid confidence values, and malformed JSON responses fail explicitly instead of being silently mapped to another code. A `review_required` taxonomy is rejected by default; `--allow-review-required` is an explicit escape hatch.

Python:

```python
from adamast import create_judge, load_traces

traces = load_traces("new_traces.jsonl")
judge = create_judge(
    "./my_tax/taxonomy.json",
    provider="anthropic",
    model="your-model-id",
)
diagnoses = judge.judge_many(traces)

for diagnosis in diagnoses:
    print(diagnosis.trace_id, diagnosis.code, diagnosis.confidence)
```

For one-off calls, `judge_trace(...)` and `judge_traces(...)` are also exported from `adamast`. JUDGES accepts the stable AdaMAST flat taxonomy schema and legacy ATLAS layered taxonomy files during migration.

JUDGES does **not** accumulate traces, regenerate or refine the taxonomy, activate new taxonomy versions, score best-of-N candidates, or install Claude Code/Codex hooks. Those policies remain separate so future adaptive generation and runtime strategies can reuse the same judge interface.

---

## How it works

Two engines run in sequence:

1. **Draft generation** — analyzes the traces (domain, structure, behavioral signals) and drafts a layered A/B/C taxonomy.
2. **Agreement refinement** — four independent annotators discover errors in the traces, reconcile them, type them A/B/C, and assign codes; disagreements go through bounded deliberation; low-agreement code definitions are rewritten and the loop repeats for up to five rounds.

The default gates:

| Gate | Default |
| --- | --- |
| macro Fleiss κ over used codes | **0.75** |
| error coverage | **0.70** |
| maximum agreement rounds | **5** |
| deliberation rounds per disagreement | **2** |

All are CLI flags (`--kappa-target`, `--coverage-floor`, `--max-rounds`, `--no-early-stop`).

---

## Providers

Model transport is independent from BASELINE and JUDGES — each workflow runs one unchanged prompt through the selected provider. Install only what you need (`pip install "adamast[bedrock]"` etc.).

| Provider | Credential environment | Model environment |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` |
| `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` |
| `google` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `GEMINI_MODEL` or `GOOGLE_MODEL` |
| `bedrock` | `AWS_BEARER_TOKEN_BEDROCK` or the standard AWS credential chain | `BEDROCK_MODEL_ID` |

The provider must be selected explicitly (`--provider` or `ADAMAST_PROVIDER`); except for OpenAI's default model, the model comes from `--model` or the model environment variable. Bedrock uses the Converse API and accepts `--aws-region` / `--aws-profile`. `--max-output-tokens` caps every model call. Provider request failures stop the run; credentials are never written to artifacts.

---

## CLI reference

```text
adamast generate
    --traces PATH                # trace file in any supported format
    --output DIR                 # output directory
    --provider NAME              # openai | anthropic | google | bedrock (or ADAMAST_PROVIDER)
    [--model MODEL]              # or the provider's model env var
    [--max-output-tokens N]      # per-call output ceiling (default 8192)
    [--aws-region R] [--aws-profile P]
    [--max-rounds N] [--kappa-target X] [--coverage-floor X] [--no-early-stop]
    [--view]                     # open taxonomy.html when done
```

```text
adamast validate SOURCE          # trace report without any model calls
adamast normalize SOURCE --output PATH
adamast view TAXONOMY [--manifest PATH] [--output PATH] [--no-open]
```

```text
adamast judge
    --taxonomy PATH              # accepted taxonomy.json
    --traces PATH                # one trace file or a directory
    --provider NAME              # openai | anthropic | google | bedrock
    [--model MODEL]
    [--output PATH]              # otherwise print the JSON document
    [--max-trace-chars N] [--max-output-tokens N]
    [--aws-region R] [--aws-profile P]
    [--allow-review-required]
```

Exit codes: `0` accepted/successful, `3` taxonomy generation requires review, `2` input, configuration, or judge-response error. `python -m adamast` works too.

---

## Layout

```text
adamast/
├── api.py                  # generate_taxonomy() — the public entry point
├── cli.py                  # `adamast` / `python -m adamast`
├── judges.py               # provider-neutral taxonomy application
├── providers.py            # OpenAI / Anthropic / Google / Bedrock transports
├── traces.py               # loader, validation, normalization
├── viewer.py               # self-contained HTML field guide
└── pipeline/
    ├── draft.py            # draft engine (ported, prompts unchanged)
    └── agreement.py        # four-annotator agreement engine (ported, prompts unchanged)
```

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The suite covers trace loading and normalization across formats, the provider adapters, generation orchestration (with the engines mocked), judge validation and batch behavior, the CLI, and the viewer — no API key needed. The full generation pipeline is exercised by running `adamast generate` against `examples/traces.jsonl`; `examples/judge_usage.py` shows the JUDGES API.

---

## Provenance

The two engines were copied from the tracked `olympiad-agents` repository (commit `67fbe490c`) and keep their original prompts and phase logic:

| Engine | Source | Here |
| --- | --- | --- |
| Draft generation | `Set-Up programs/1_taxonomy_generation/LLM_Nomos.py` | `adamast/pipeline/draft.py` |
| Agreement process | `Set-Up programs/1_taxonomy_generation/MATRS_taxonomy_refiner.py` | `adamast/pipeline/agreement.py` |

AdaMAST adds the integration code around them: the trace normalizer, the provider adapters, the draft-to-agreement schema adapter, explicit acceptance status and manifests, the stable public taxonomy shape, and the viewer.

The initial JUDGES contract was migrated from the standalone classifier in the main ATLAS repository. AdaMAST keeps the same single-best-code diagnosis purpose while replacing ATLAS's model-name-based routing and silent closest-code fallback with the shared provider abstraction and explicit output validation. The ATLAS repository remains unchanged.
