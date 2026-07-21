# AdaMAST documentation

This folder is the source for the public AdaMAST documentation. The web guide
is ordered from standalone taxonomy generation to adaptive host integration.

## Start here

| Goal | Page |
|---|---|
| Install AdaMAST | [index.md](index.md#install-adamast) |
| Choose the right workflow | [CONCEPTS.md](CONCEPTS.md) |
| Prepare and validate traces | [TRACE_FORMATS.md](TRACE_FORMATS.md) |
| Generate a BASELINE taxonomy | [BASELINE_GENERATION.md](BASELINE_GENERATION.md) |
| Understand inter-annotator agreement | [AGREEMENT_GATE.md](AGREEMENT_GATE.md) |
| Inspect outputs in the browser field guide | [TAXONOMY_OUTPUTS.md](TAXONOMY_OUTPUTS.md) |
| Configure OpenAI, Anthropic, Google, or Bedrock | [PROVIDERS.md](PROVIDERS.md) |
| Judge new traces | [JUDGING.md](JUDGING.md) |
| Choose among specialized judges | [JUDGE_TYPES.md](JUDGE_TYPES.md) |
| Add the adaptive runtime | [GETTING_STARTED.md](GETTING_STARTED.md) |

## Adaptive integrations

| Integration | Page |
|---|---|
| Direct single-LLM calls, scripts, notebooks, benchmarks | [SINGLE_LLM.md](SINGLE_LLM.md) |
| Harness-author contract, privacy, and redaction | [INTEGRATION.md](INTEGRATION.md) |
| Runtime API reference | [API_OR_RUNTIME.md](API_OR_RUNTIME.md) |
| Codex | [CODEX.md](CODEX.md) |
| Claude Code | [CLAUDE_CODE.md](CLAUDE_CODE.md) |

## Runtime data

| Topic | Page |
|---|---|
| Taxonomy records, inheritance, and importing existing taxonomies | [TAXONOMIES.md](TAXONOMIES.md) |
| Trace storage, generation thresholds, refinement thresholds | [TRACES_AND_LEARNING.md](TRACES_AND_LEARNING.md) |
| Live dashboard, task UID filtering, and local monitoring | [DASHBOARD.md](DASHBOARD.md) |
| Local dashboard HTTP endpoints and response shapes | [WEB_API.md](WEB_API.md) |
| Program health CLI and common runtime failures | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Supported hosts and current limits | [COMPATIBILITY.md](COMPATIBILITY.md) |

## Web docs

The documentation website at
[multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs](https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs/)
is built from these Markdown pages with MkDocs Material. The project root at
`/AdaMAST/` is a separate placeholder sourced from [`website/`](../website/).
[`scripts/build_site.py`](../scripts/build_site.py) assembles the complete Pages
tree, and the public [`docs` workflow](../.github/workflows/docs.yml) deploys it.

The browser documentation home is [index.md](index.md); this README remains the
index for GitHub's file view.
