# AdaMAST documentation

AdaMAST builds failure-mode taxonomies from agent traces, checks whether
independent annotators can apply them consistently, and reuses the resulting
taxonomy for evaluation or runtime guidance.

The documentation is ordered from the smallest standalone workflow to the most
involved integration. You do **not** need Codex, Claude Code, or an agent harness
to generate a taxonomy or judge a trace.

## Install AdaMAST

### Requirements

- Python 3.10 or newer
- a JSON or JSONL trace file, or a directory containing those files
- credentials for one supported model provider when running generation or a
  model-backed judge

### Install the current public package

```bash
git clone https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST.git
cd AdaMAST
python -m pip install -e ".[all]"
```

`[all]` installs the OpenAI, Anthropic, Google, and AWS Bedrock adapters. To
install only one adapter, replace it with `[openai]`, `[anthropic]`, `[google]`,
or `[bedrock]`.

For documentation development, include the docs dependency:

```bash
python -m pip install -e ".[all,docs]"
```

### Verify the installation

```bash
adamast --help
adamast validate examples/traces.jsonl
```

The validation command performs no model calls. It reports the trace count,
detected formats, input files, and empty trajectories.

## Generate your first taxonomy

Set one provider credential and choose a model. This example uses OpenAI; the
same workflow supports Anthropic, Google, and AWS Bedrock.

```bash
export OPENAI_API_KEY="..."

adamast generate \
  --provider openai \
  --model gpt-5-nano \
  --traces examples/traces.jsonl \
  --output ./my-taxonomy \
  --view
```

AdaMAST normalizes the traces, drafts the A/B/C failure taxonomy, runs the
four-annotator agreement process, writes an auditable artifact bundle, and
opens a read-only browser field guide. Start with
[Prepare traces](TRACE_FORMATS.md) when using your own data, or go directly to
[Generate a taxonomy](BASELINE_GENERATION.md) when the trace file already
validates.

## Choose a workflow

| Level | Goal | Start with |
| --- | --- | --- |
| **01 · Foundation** | Generate and agreement-check a standalone taxonomy from completed traces | [Prepare traces](TRACE_FORMATS.md) |
| **02 · Evaluation** | Apply a taxonomy to new traces or select a specialized judge | [Judge traces](JUDGING.md) |
| **03 · Adaptive runtime** | Accumulate traces and refine the active taxonomy over time | [Runtime overview](GETTING_STARTED.md) |
| **04 · Host integration** | Install the adaptive runtime into Codex or Claude Code | [Codex](CODEX.md) or [Claude Code](CLAUDE_CODE.md) |

## Core concepts

### BASELINE

**BASELINE** is the standalone generation path. It takes completed traces,
creates a taxonomy, and runs the full inter-annotator agreement layer. It does
not install hooks or maintain runtime state.

### JUDGES

**JUDGES** applies an existing taxonomy to new evidence. The core judge returns
one validated, evidence-backed failure code per trace. More specialized judges
cover mapping, coverage, taxonomy quality, calibration, and causal reflection.

### Adaptive runtime

The runtime path records new traces, keeps a taxonomy active at task boundaries,
and starts generation or refinement when configured thresholds are reached.
Single-model programs and custom harnesses come before the host-specific Codex
and Claude Code installers in this guide.

## What to read next

- [Trace formats](TRACE_FORMATS.md) lists every accepted input shape and the
  canonical normalized record.
- [Agreement gate](AGREEMENT_GATE.md) explains the four annotators, Fleiss
  kappa, coverage, and `review_required` results.
- [Providers and models](PROVIDERS.md) covers credentials and model selection
  for OpenAI, Anthropic, Google, and Bedrock.
- [Taxonomy outputs](TAXONOMY_OUTPUTS.md) explains `taxonomy.json`, the manifest,
  intermediate artifacts, and the browser field guide.

The research method and evaluation are described in
[Fantastic Adaptive Taxonomies and How to Use Them](https://arxiv.org/abs/2607.16387).
