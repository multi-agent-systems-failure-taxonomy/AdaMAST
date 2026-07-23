# AdaMAST

<p align="center">
  <b>Find out how your AI agents fail — from their own work.</b>
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2607.16387"><img src="https://img.shields.io/badge/paper-arXiv-B31B1B?style=flat-square&logo=arxiv&logoColor=white" alt="Paper" /></a>
  <a href="https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs/"><img src="https://img.shields.io/badge/docs-website-2457D6?style=flat-square" alt="Docs" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-1F8A70?style=flat-square" alt="License" /></a>
</p>

AI agents — coding assistants, tool-using pipelines, multi-agent systems — don't fail randomly. Each system tends to fail in its own **recurring, recognizable ways**: the checker that always waves work through, the plan that quietly drops a requirement, the tool result that gets ignored. Most teams have no good way to name those patterns, count them, or watch them change.

**AdaMAST** reads the logs of your agent's past runs and automatically builds a **catalog of that system's failure patterns** (we call it a *taxonomy*), with every entry backed by real quotes from your own logs. You can then use the catalog to grade new runs, spot regressions, and feed improvement loops with *what went wrong and why* — instead of just a score.

- 📄 **Works on the logs you already have** — common agent log formats are auto-detected
- 🔍 **Every failure pattern comes with evidence** — verbatim quotes from real runs, not guesses
- ✅ **Catalogs are quality-gated** — several independent automated reviews must agree before one is accepted
- 🔌 **Optional live mode** — plug into Codex or Claude Code and the catalog is learned and applied while you work

**Paper:** [Fantastic Adaptive Taxonomies and How to Use Them](https://arxiv.org/abs/2607.16387) · **Docs:** [Website](https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs/)

---

## 🧪 How it works

1. **Generate.** Several independent automated annotators each read your traces and propose failure patterns. The proposals are reconciled, and a catalog is accepted only when the independent annotations agree with each other — otherwise it is redrafted. You get `taxonomy.json`, an agreement `manifest.json`, and a browsable field guide. (The full protocol and its acceptance criteria are in the [paper](https://arxiv.org/abs/2607.16387).)
2. **Judge.** Apply an accepted taxonomy to new runs: each trace gets the single best-matching failure code, with cited evidence.
3. **Trust it.** Every code carries verbatim evidence from real traces, and the agreement manifest records how the independent annotations converged.

Learned codes sit on three stable axes:

| Axis | Scope | Example |
|---|---|---|
| ⚙️ System-level | Can arise in any agent system | Context exhaustion |
| 🎭 Role-specific | Tied to a discovered component role | Checker rubber-stamps solver output |
| 🧪 Domain-specific | Requires task knowledge | Algorithm mismatch |

Until you generate one, the built-in 14-code adaptation of MAST (["Why Do Multi-Agent LLM Systems Fail?"](https://arxiv.org/abs/2503.13657), Cemri et al., 2025) serves as the general-purpose floor.

## 📦 Install

Requirements: Python 3.10+.

```bash
pip install adamast
```

Verify, with the bundled examples (no model calls):

```bash
python -m adamast.examples
adamast validate adamast-examples/traces.jsonl
```

## 🚀 Use it

Set one provider credential (OpenAI shown; Anthropic, Google, and AWS Bedrock work the same — [Providers](docs/PROVIDERS.md)):

```bash
export OPENAI_API_KEY="..."
```

**Generate a taxonomy** from a trace file or folder (any of the 8 auto-detected formats):

```bash
adamast generate --traces adamast-examples/traces.jsonl --output ./my-taxonomy --view
```

**Judge new traces** with it:

```bash
adamast judge --taxonomy ./my-taxonomy/taxonomy.json --traces ./new_traces --output judgments.json
```

**The everyday commands:**

| Command | Purpose |
|---|---|
| `adamast validate <traces>` | Check trace files: count, detected formats, empty trajectories |
| `adamast normalize <traces> --output out.jsonl` | Convert any accepted format to canonical AdaMAST JSONL |
| `adamast generate --traces … --output …` | Agreement-gated taxonomy generation |
| `adamast judge --taxonomy … --traces …` | One best code per trace, with evidence |
| `adamast view <taxonomy.json>` | Open a taxonomy as a read-only browser field guide |

Deeper guides: [Trace formats](docs/TRACE_FORMATS.md) · [Generation](docs/BASELINE_GENERATION.md) · [The agreement gate](docs/AGREEMENT_GATE.md) · [Judging](docs/JUDGING.md) · [Judge types](docs/JUDGE_TYPES.md) · [Outputs](docs/TAXONOMY_OUTPUTS.md)

## 🔌 Runtime integration

AdaMAST can also ride along **live** inside Codex or Claude Code: hooks checkpoint the agent's work at natural boundaries, record evidence, and learn a project-specific taxonomy automatically from completed conversations — no API key or config needed for the interactive path.

```bash
# once, for the host you use
adamast claude install --user-level
adamast codex install --user-level

# health check and live monitor
adamast doctor
adamast dashboard
```

The full story — how checkpoints work, the taxonomy picker, background learning, the live monitor, and every knob — lives in **[the runtime integration guide](RUNTIME_INTEGRATION.md)**.

## 📚 Learn more

| You want to… | Read |
|---|---|
| Prepare and check trace files | [Trace formats](docs/TRACE_FORMATS.md) |
| Understand the words (trace, taxonomy, judge, …) | [Concepts](docs/CONCEPTS.md) |
| Use the Python API instead of the CLI | [Runtime API](docs/INTEGRATION.md) |
| Fix a broken setup | [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Browse everything | [Documentation index](docs/README.md) |

<details>
<summary><b>🧰 All commands</b></summary>

| Command | Purpose |
|---|---|
| `adamast validate` / `normalize` | Check and convert trace files |
| `adamast generate` | Agreement-gated taxonomy generation |
| `adamast judge` | Apply a taxonomy to traces |
| `adamast view` | Browser field guide for one taxonomy |
| `adamast find` | List or select stored taxonomies |
| `adamast import-traces` | Generate a taxonomy from existing traces into the local store |
| `adamast doctor` | Validate paths, configuration, hooks, and host contracts |
| `adamast status` | Active taxonomy, traces, learning state, recent decisions |
| `adamast dashboard` | Local taxonomy dashboard / checkpoint monitor |
| `adamast traces` | Inspect trace state |
| `adamast claude install` / `uninstall` | Manage Claude Code hooks |
| `adamast codex install` / `uninstall` | Manage Codex hooks |
| `adamast single-run` | Wrap one direct model task with AdaMAST |

</details>

<details>
<summary><b>🗂️ Repository map</b></summary>

| Path | Responsibility |
|---|---|
| [`adamast/core/`](adamast/core/) | Taxonomy data model, evidence, traces, taxonomy store/MAST/resolution, session lifecycle |
| [`adamast/protocol/`](adamast/protocol/) | The compact-checkpoint implementation and the pre-submission gate |
| [`adamast/judges/`](adamast/judges/) | Taxonomy and reflection judges, plus the provider-neutral JUDGES contract |
| [`adamast/llm/`](adamast/llm/) | Model routing, learning calls, and provider transports |
| [`adamast/learning/`](adamast/learning/) | Taxonomy generation and refinement, learning jobs, and the vendored/ported pipelines |
| [`adamast/hosts/`](adamast/hosts/) | Claude Code, Codex, interactive, and single-LLM host adapters |
| [`adamast/dashboard/`](adamast/dashboard/) | Local dashboard, status, taxonomy viewer, and web views |
| [`adamast/examples/`](adamast/examples/) | Runnable demonstrations (`python -m adamast.examples` copies them locally) |
| [`adamast/cli.py`](adamast/cli.py) | The umbrella `adamast` command |
| [`tests/`](tests/) | The single test suite (`python -m pytest tests`) |
| [`docs/`](docs/) | User and contributor documentation ([index](docs/README.md)) |
| [`scripts/`](scripts/) | Repository tooling: docs-site build, public publishing |
| [`website/`](website/) | The static landing page served ahead of the docs |
| [`SKILL.md`](SKILL.md) | The Codex skill manifest for AdaMAST |

Everything importable lives in the `adamast` package; the complete ownership
rules are in [Architecture](docs/ARCHITECTURE.md).

</details>

## 🤝 Contributing

Development setup, verification commands, and package boundaries: [CONTRIBUTING.md](CONTRIBUTING.md) · Release steps: [RELEASING.md](RELEASING.md)

The original research pipeline lives on the
[`paper-pipeline`](https://github.com/multi-agent-systems-failure-taxonomy/ATLAS/tree/paper-pipeline)
branch; a maintained, locally patched fork is vendored under
[`adamast/learning/vendor/`](adamast/learning/vendor/) with provenance in
[`VENDORED.md`](adamast/learning/vendor/VENDORED.md).

## 📄 License

Apache-2.0. See [LICENSE](LICENSE).
