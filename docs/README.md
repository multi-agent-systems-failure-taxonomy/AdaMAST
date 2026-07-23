# AdaMAST documentation

This folder is the detailed reference for AdaMAST. Find the row that matches
your goal and open that page; the web documentation is ordered from
standalone taxonomy generation to adaptive host integration.

## 🚀 Start here

| Goal | Page |
|---|---|
| Install AdaMAST | [index.md](index.md#install-adamast) |
| Pick an install shape: provider extras, source checkout | [INSTALLATION.md](INSTALLATION.md) |
| Choose the right workflow | [CONCEPTS.md](CONCEPTS.md) |
| Prepare and validate traces | [TRACE_FORMATS.md](TRACE_FORMATS.md) |
| Generate a taxonomy | [GENERATION.md](GENERATION.md) |
| Understand inter-annotator agreement | [AGREEMENT_GATE.md](AGREEMENT_GATE.md) |
| Inspect outputs in the browser field guide | [TAXONOMY_OUTPUTS.md](TAXONOMY_OUTPUTS.md) |
| Configure OpenAI, Anthropic, Google, or Bedrock | [PROVIDERS.md](PROVIDERS.md) |
| Judge new traces | [JUDGING.md](JUDGING.md) |
| Choose among specialized judges | [JUDGE_TYPES.md](JUDGE_TYPES.md) |
| Add the adaptive runtime | [GETTING_STARTED.md](GETTING_STARTED.md) |

## 🔌 Adaptive integrations

| Integration | Page |
|---|---|
| The live runtime end to end: install, checkpoints, learning, knobs | [RUNTIME_INTEGRATION.md](RUNTIME_INTEGRATION.md) |
| Direct single-LLM calls, scripts, notebooks, benchmarks | [SINGLE_LLM.md](SINGLE_LLM.md) |
| Runtime API reference | [API_OR_RUNTIME.md](API_OR_RUNTIME.md) |
| Harness-author contract, privacy, and redaction | [INTEGRATION.md](INTEGRATION.md) |
| Choose an interactive host | [INTERACTIVE_SETUP.md](INTERACTIVE_SETUP.md) |
| Codex | [CODEX.md](CODEX.md) |
| Claude Code | [CLAUDE_CODE.md](CLAUDE_CODE.md) |
| Native in-host taxonomy learning (no API key) | [NATIVE_LEARNING.md](NATIVE_LEARNING.md) |
| Follow one supervised task end to end | [EXAMPLE_RUN.md](EXAMPLE_RUN.md) |

## 🗃️ Runtime data

| Topic | Page |
|---|---|
| The canonical `adamast.json` configuration reference | [CONFIGURATION.md](CONFIGURATION.md) |
| Taxonomy records, inheritance, and importing existing taxonomies | [TAXONOMIES.md](TAXONOMIES.md) |
| Trace storage, generation thresholds, refinement thresholds | [TRACES_AND_LEARNING.md](TRACES_AND_LEARNING.md) |
| Live project/conversation checkpoint monitoring | [DASHBOARD.md](DASHBOARD.md) |
| Local dashboard HTTP endpoints and response shapes | [WEB_API.md](WEB_API.md) |
| Program health CLI and common runtime failures | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Supported hosts and current limits | [COMPATIBILITY.md](COMPATIBILITY.md) |

## 🌐 Web docs

The documentation website at
[multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs](https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs/)
is built from these Markdown pages with MkDocs Material (see
[mkdocs.yml](../mkdocs.yml)). The project root at `/AdaMAST/` is a separate
placeholder sourced from [website/](../website/); the complete Pages tree is
assembled by [scripts/build_site.py](../scripts/build_site.py).

> 📦 **In this private repository**, GitHub Actions builds the site strictly
> and stores it as a workflow artifact, but does not deploy it to GitHub
> Pages. Public deployment belongs to the public repository after the
> reviewed changes are transferred.

The docs home is [index.md](index.md); this README stays the index for
GitHub's file view.

## 🧩 Lower-level package maps

These pages are useful when changing internals:

- [CUSTOMIZATION.md](CUSTOMIZATION.md): change what AdaMAST says, decides, or enforces, one asset file at a time
- [ARCHITECTURE.md](ARCHITECTURE.md): package layout, event flow, and the ownership rules between engine and hosts
- [adamast/hosts/interactive/README.md](../adamast/hosts/interactive/README.md)
- [adamast/hosts/claude_code/README.md](../adamast/hosts/claude_code/README.md)
- [adamast/hosts/codex/README.md](../adamast/hosts/codex/README.md)
- [adamast/judges/reflection_judge/README.md](../adamast/judges/reflection_judge/README.md)
- [tests/README.md](../tests/README.md)

Continue with [index.md](index.md) (install AdaMAST and generate a first
taxonomy) or with [CONCEPTS.md](CONCEPTS.md) to choose a workflow.
