# AdaMAST

> **Private development repository.** This tree is laid out exactly as the
> public AdaMAST repository should look; publishing is a filtered copy that
> drops only the private paths listed in [`publish.exclude`](publish.exclude).
> See [the publishing workflow](docs/PUBLISHING.md).

### Failure-mode taxonomies for agents, grounded in the traces they actually produce.

[![Paper](https://img.shields.io/badge/paper-arXiv-B31B1B?logo=arxiv&logoColor=white)](https://arxiv.org/abs/2607.16387)
[![Docs](https://img.shields.io/badge/docs-website-2457D6)](https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-1F8A70)](LICENSE)

AdaMAST adds a diagnostic feedback layer to an agent. It checks work at
meaningful boundaries, records evidence about recurring failures, and learns a
project-specific taxonomy from completed traces. Your existing agent or
harness keeps owning the task.

**Paper:** [Fantastic Adaptive Taxonomies and How to Use Them](https://arxiv.org/abs/2607.16387)

**Documentation:** [Website](https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs/) · [Interactive setup](docs/INTERACTIVE_SETUP.md) · [Architecture](docs/ARCHITECTURE.md)

## Install in one minute

Requirements: Python 3.10 or newer.

```bash
pip install adamast
```

Install AdaMAST once for the host you use:

**Codex**

```bash
adamast codex install --user-level
adamast doctor --codex
```

**Claude Code**

```bash
adamast claude install --user-level
adamast doctor --claude-code
```

Every command is also available as a standalone script
(`adamast-claude-install`, `adamast-doctor`, ...); `adamast --help` lists
the full command surface.

Fully quit and reopen an already-running Codex Desktop or Claude Code process
after installing or updating hooks, then start a **new conversation**. AdaMAST
opens the local taxonomy library and lets you choose:

- **MAST** to begin with the built-in general taxonomy;
- a **stored taxonomy** to share the project's learned vocabulary;
- **No taxonomy** to disable AdaMAST for that conversation.

No `adamast.json`, external model API key, standalone host CLI, or second login
is required for this interactive path. Run both installers to enable AdaMAST in
both hosts. Every new Codex or Claude Code conversation receives its own
conversation branch: its program, traces, learning job, and active taxonomy
head cannot be claimed by another conversation or by the other host.

## What happens in a conversation

1. AdaMAST resolves the Git project and task group, then pins one taxonomy.
2. The agent continues normal work. Checkpoints inspect only recent activity.
3. A completed assistant episode becomes one canonical trace.
4. At five eligible traces, AdaMAST queues taxonomy generation by default.
5. One native host subagent proposes a candidate while the main agent keeps
   working. After exact-span checks, a separate support-review subagent must
   approve every replacement code before foreground activation.
6. The first refinement review occurs after ten additional traces; later
   reviews occur every twenty traces by default.

Stored taxonomies and MAST are immutable branch seeds. Selecting either starts
an isolated conversation program. Later generation or refinement uses only
that conversation's traces and advances only that branch.

Learn how this is kept durable and race-safe in
[Native taxonomy learning](docs/NATIVE_LEARNING.md).

## Choose your integration

| Goal | Start here |
|---|---|
| Use AdaMAST in every Codex task | `adamast-codex-install --user-level` · [Codex guide](docs/CODEX.md) |
| Use AdaMAST in every Claude Code session | `adamast-claude-install --user-level` · [Claude Code guide](docs/CLAUDE_CODE.md) |
| Configure hooks for one repository | [Project setup](docs/GETTING_STARTED.md) |
| Wrap one direct model call | `adamast-single-run` · [Single LLM guide](docs/SINGLE_LLM.md) |
| Learn from an existing trace folder | `adamast-import-traces` · [Taxonomies](docs/TAXONOMIES.md) |
| Integrate a custom agent harness | `from adamast import start_session` · [Runtime API](docs/INTEGRATION.md) |
| Inspect an example without configuring a provider | `python -m examples.dashboard_demo` · [Example run](docs/EXAMPLE_RUN.md) |

## Runtime loop

![AdaMAST runtime loop](docs/adamast_runtime_loop.png)

At a checkpoint, the agent follows a fixed sequence:

```text
Observe:   What concretely happened or was omitted?
Correlate: Which evidence-supported cause explains it?
Map:       Which active failure code applies, if any?
Decide:    Continue, or make one focused repair.
```

`none apply` is valid. AdaMAST does not manufacture a failure just to force a
change. Built-in Codex and Claude Code gates send compact checkpoint fields to
a private recorder; the conversation shows the task answer while the local
monitor shows checkpoint evidence. Claude may still block a boundary whose
private checkpoint requires repair.

## Why adaptive taxonomies

Improvement procedures need feedback that preserves *why* a trajectory failed.
Scalar rewards discard the reason. Free-form reflection is difficult to
aggregate. A fixed taxonomy cannot know the target agent's roles, tools, or
domain before observing it.

AdaMAST learns a compact set of evidence-grounded failure codes from the target
system's own traces. Until a learned taxonomy is active, runs start from the
built-in 14-code adaptation of MAST from
["Why Do Multi-Agent LLM Systems Fail?" (Cemri et al., 2025)](https://arxiv.org/abs/2503.13657).

Generated codes are organized along three stable axes:

| Axis | Scope | Example |
|---|---|---|
| System-level | Can arise in any agent system | Context exhaustion |
| Role-specific | Tied to a discovered component role | Checker rubber-stamps solver output |
| Domain-specific | Requires task knowledge | Algorithm mismatch |

The paper evaluates this vocabulary as feedback for best-of-N selection,
evolutionary agent optimization, and runtime reflection. On TRAIL, induced
codes align with expert annotations at Cohen's kappa 0.725.

## Repository map

| Path | Responsibility |
|---|---|
| [`adamast/core/`](adamast/core/) | Taxonomy data model, evidence, traces, reflection parsing, taxonomy store/MAST/resolution, session lifecycle |
| [`adamast/protocol/`](adamast/protocol/) | The one compact-checkpoint implementation and the pre-submission gate |
| [`adamast/judges/`](adamast/judges/) | Taxonomy and reflection judges, plus the provider-neutral JUDGES contract |
| [`adamast/llm/`](adamast/llm/) | Model routing, learning calls, and provider transports |
| [`adamast/learning/`](adamast/learning/) | Taxonomy generation and refinement, learning jobs, and the vendored/ported pipelines |
| [`adamast/hosts/`](adamast/hosts/) | Claude Code, Codex, interactive, and single-LLM host adapters |
| [`adamast/dashboard/`](adamast/dashboard/) | Local dashboard, status, taxonomy viewer, and web views |
| [`adamast/cli.py`](adamast/cli.py) | The umbrella `adamast` command |
| [`tests/`](tests/) | The single test suite for everything above (`python -m pytest tests`) |
| [`docs/`](docs/) | User and contributor documentation ([index](docs/README.md)) |
| [`examples/`](examples/) | Runnable demonstrations |
| [`runs/`](runs/) | Evaluation artifacts and reproduction notes |
| [`scripts/`](scripts/) | Repository tooling: docs-site build, public publishing |
| [`website/`](website/) | The static landing page served ahead of the docs |
| [`SKILL.md`](SKILL.md) | The Codex skill manifest for AdaMAST |

Everything importable lives in the `adamast` package; the complete
ownership rules are in [Architecture](docs/ARCHITECTURE.md).

## Results

Reported summaries, exact taxonomies, and reproduction instructions live in
[`runs/`](runs/). Per-question rows and raw scorer output are not included, so
the headline numbers below cannot be independently recomputed from this
repository alone.

| Experiment | Headline |
|---|---|
| [OfficeQA Pro](runs/OfficeQA/) | 44.4% → **51.9%** official scorer, same 133-question harness in both arms |
| [Circle packing, n=26](runs/Circle-Packing/) | AdaMAST-guided search reaches 0.997 of the AlphaEvolve record in **20 evaluations** |

The paper reports AdaMAST-Judge at 89.9% accuracy on Terminal-Bench 2.0 and an
87.9% to 91.9% held-out improvement for evolutionary optimization on a
655-problem set.

## Documentation

| Need | Page |
|---|---|
| First interactive install | [Interactive setup](docs/INTERACTIVE_SETUP.md) |
| See a complete run | [Example run](docs/EXAMPLE_RUN.md) |
| Understand terms | [Concepts](docs/CONCEPTS.md) |
| Understand code ownership | [Architecture](docs/ARCHITECTURE.md) |
| Understand native workers | [Native taxonomy learning](docs/NATIVE_LEARNING.md) |
| Configure one project | [Getting started](docs/GETTING_STARTED.md) |
| Look up every field | [Configuration reference](docs/CONFIGURATION.md) |
| Debug setup | [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Browse all docs | [Documentation index](docs/README.md) |

## Main commands

| Command | Purpose |
|---|---|
| `adamast doctor` | Validate paths, configuration, hooks, and host contracts. |
| `adamast status` | Show the active taxonomy, traces, learning state, and recent decisions. |
| `adamast find` | List or select stored taxonomies. |
| `adamast dashboard` | Open the local taxonomy dashboard or shared Codex/Claude checkpoint monitor. |
| `adamast traces` | Inspect trace state. |
| `adamast import-traces` | Generate a taxonomy from existing traces. |
| `adamast codex install` / `adamast codex uninstall` | Manage Codex hooks. |
| `adamast claude install` / `adamast claude uninstall` | Manage Claude Code hooks. |
| `adamast single-run` | Wrap one direct model task with AdaMAST. |

Each also exists as a standalone `adamast-*` script (`adamast-doctor`,
`adamast-claude-install`, ...) for scripts and hooks that predate the
umbrella command.

## Contributing

Development setup, verification commands, and package boundaries are in
[CONTRIBUTING.md](CONTRIBUTING.md). Release steps are in
[RELEASING.md](RELEASING.md).

The original research pipeline is available on the
[`paper-pipeline`](https://github.com/multi-agent-systems-failure-taxonomy/ATLAS/tree/paper-pipeline)
branch. A maintained, locally patched fork is included under
[`adamast/learning/vendor/`](adamast/learning/vendor/); its provenance and
change categories are documented in
[`VENDORED.md`](adamast/learning/vendor/VENDORED.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
