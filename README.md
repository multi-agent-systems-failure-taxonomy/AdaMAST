# AdaMAST

<p align="center">
  <b>Learn how your AI agents fail, from their own recorded work.</b>
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2607.16387"><img src="https://img.shields.io/badge/paper-arXiv-B31B1B?style=flat-square&logo=arxiv&logoColor=white" alt="Paper" /></a>
  <a href="https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/"><img src="https://img.shields.io/badge/website-AdaMAST-2D5BCE?style=flat-square" alt="Website" /></a>
  <a href="https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs/"><img src="https://img.shields.io/badge/docs-reference-2457D6?style=flat-square" alt="Docs" /></a>
  <a href="https://pypi.org/project/adamast/"><img src="https://img.shields.io/pypi/v/adamast?style=flat-square" alt="PyPI" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-1F8A70?style=flat-square" alt="License" /></a>
</p>

AI agents (coding assistants, tool-using pipelines, multi-agent systems) don't fail randomly. Each system tends to fail in its own **recurring, recognizable ways**: the checker that always waves work through, the plan that quietly drops a requirement, the tool result that gets ignored. Most teams have no good way to name those patterns, count them, or watch them change.

**AdaMAST** reads the logs of your agent's past runs and automatically builds a **catalog of that system's failure patterns** (we call it a *taxonomy*), with every entry backed by real quotes from your own logs. You can then use the catalog to grade new runs, spot regressions, and feed improvement loops with *what went wrong and why* instead of just a score.

- 📄 **Works on the logs you already have.** Common agent log formats are auto-detected
- 🔍 **Every failure pattern comes with evidence.** Verbatim quotes from real runs
- ✅ **Catalogs are quality-gated.** Several independent automated reviews must agree before one is accepted
- 🔌 **Live mode.** Plug into Codex or Claude Code and the catalog is learned and applied while you work

**Paper:** [Fantastic Adaptive Taxonomies and How to Use Them](https://arxiv.org/abs/2607.16387) · **Website:** [AdaMAST](https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/) · **Blog:** [AdaMAST announcement](https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/blogs/adamast_paper/) · **Docs:** [Reference](https://multi-agent-systems-failure-taxonomy.github.io/AdaMAST/docs/)

---

## 🧪 How it works

`traces → independent annotators → agreement gate → accepted taxonomy → judge new runs`

- **Propose.** Several independent automated annotators read your traces, and each proposes failure patterns on its own.
- **Agree.** The proposals are reconciled. A catalog is accepted only when the independent annotations agree with each other; otherwise it is redrafted. (The full protocol and its acceptance criteria are in the [paper](https://arxiv.org/abs/2607.16387).)
- **Apply.** Judge new runs against the accepted catalog: each trace gets its best-matching failure code, with verbatim evidence quoted from the run.

Every entry in the catalog belongs to one of three categories:

| Category | Scope | Example |
|---|---|---|
| ⚙️ System-level | Can arise in any agent system | Context exhaustion |
| 🎭 Role-specific | Tied to a discovered component role | Checker rubber-stamps solver output |
| 🧪 Domain-specific | Requires task knowledge | Algorithm mismatch |

## 💡 Use cases

| Scenario | How |
|---|---|
| 🔬 **Error analysis**: learn what your agent actually gets wrong, with supporting evidence | `adamast generate` on a batch of traces, then read the field guide |
| 📈 **Regression tracking**: watch failure patterns across agent versions | `adamast judge` new runs against the same catalog and compare |
| 🏅 **Best-of-N selection**: pick the cleanest of several candidate runs | Judge each candidate; prefer the one with the fewest, least severe codes |
| 🧬 **Feedback for optimization loops**: tell a prompt or agent optimizer *why* runs failed, not just the score | Feed the judged codes back as the improvement signal |
| 🔌 **Live runtime integration**: the catalog is learned and applied while you work in Codex or Claude Code | The one that needs setup; see [Runtime integration](#-runtime-integration) |

## 📦 Install

**Running AdaMAST live inside your coding agent?** Install the native plugin;
nothing needs to be set up first:

```
/plugin marketplace add multi-agent-systems-failure-taxonomy/AdaMAST
/plugin install adamast@adamast
```
```bash
codex plugin marketplace add multi-agent-systems-failure-taxonomy/AdaMAST
codex plugin add adamast@adamast
```

The first block is Claude Code, the second Codex. Both install hooks and the
guidance skill, then learn a taxonomy from your own conversations. Details and
the package-based alternative are under
[Runtime integration](#-runtime-integration).

**Using the CLI to generate or judge taxonomies from trace files?** Requirements:
Python 3.10+.

```bash
pip install adamast
```

Verify, with the bundled examples (no model calls):

```bash
python -m adamast.examples
adamast validate adamast-examples/traces.jsonl
```

## 🚀 Use it

Every command below runs against the bundled examples, so they work as written
after `python -m adamast.examples`.

Set one provider credential. OpenAI is the default, so no `--provider` flag is
needed; Anthropic, Google, and AWS Bedrock work the same way with
`--provider` or `ADAMAST_PROVIDER` (see [Providers](docs/PROVIDERS.md)):

```bash
export OPENAI_API_KEY="..."
```

**Generate a taxonomy** from a trace file or folder (any of the 7 auto-detected formats):

```bash
adamast generate --traces adamast-examples/traces.jsonl --output ./my-taxonomy --view
```

**Judge new traces** with it:

```bash
adamast judge --taxonomy ./my-taxonomy/taxonomy.json --traces adamast-examples/traces.jsonl --output judgments.json
```

A ready-made taxonomy ships too, so judging works without waiting on generation:

```bash
adamast judge --taxonomy adamast-examples/taxonomy.sample.json --traces adamast-examples/traces.jsonl --output judgments.json
```

**The everyday commands**, each runnable as written against the bundled examples:

| Command | Purpose |
|---|---|
| `adamast validate adamast-examples/traces.jsonl` | Check trace files: count, detected formats, empty trajectories |
| `adamast normalize adamast-examples/traces.jsonl --output out.jsonl` | Convert any accepted format to canonical AdaMAST JSONL |
| `adamast generate --traces adamast-examples/traces.jsonl --output ./my-taxonomy` | Agreement-gated taxonomy generation |
| `adamast judge --taxonomy adamast-examples/taxonomy.sample.json --traces adamast-examples/traces.jsonl` | Every supported failure code per trace, with evidence |
| `adamast view adamast-examples/taxonomy.sample.json` | Open a taxonomy as a read-only browser field guide |

Only `generate` and `judge` call a model; `validate`, `normalize`, and `view`
need no credential.

Deeper guides: [Trace formats](docs/TRACE_FORMATS.md) · [Generation](docs/GENERATION.md) · [The agreement gate](docs/AGREEMENT_GATE.md) · [Judging](docs/JUDGING.md) · [Judge types](docs/JUDGE_TYPES.md) · [Outputs](docs/TAXONOMY_OUTPUTS.md)

## 🔌 Runtime integration

AdaMAST can also run **live** inside Codex or Claude Code: hooks checkpoint the agent's work at natural boundaries, record evidence, and learn a project-specific taxonomy automatically from completed conversations. No API key or config is needed for the interactive path. Until your project's own catalog is learned, conversations start from a built-in adaptation of the MAST taxonomy (["Why Do Multi-Agent LLM Systems Fail?"](https://arxiv.org/abs/2503.13657), Cemri et al., 2025).

### Claude Code

Two paths. Pick one — both register the same hooks, so do not run both.

**A · Plugin (recommended).** Nothing to install first:

```
/plugin marketplace add multi-agent-systems-failure-taxonomy/AdaMAST
/plugin install adamast@adamast
```

The plugin ships the skill, hooks, and taxonomy subagent together. On first use
it installs its version-pinned runtime privately, so Python, `pip`, `uv`, and
the `claude` CLI do not need to be installed beforehand. See
[the plugin README](plugins/adamast/README.md).

**B · Package CLI.** Prefer this if you already manage AdaMAST as a dependency,
pin versions, or want a project-local install:

```bash
uv tool install adamast          # or: pip install adamast
adamast claude install --user-level
```

Requires the `claude` CLI binary on `PATH` — the installer verifies the hook
contract against it and aborts without it.

### Codex

**A · Plugin (recommended).** Nothing to install first:

```bash
codex plugin marketplace add multi-agent-systems-failure-taxonomy/AdaMAST
codex plugin add adamast@adamast
```

Open `/hooks` in Codex and trust the new plugin hooks.

**B · Package CLI.** Use this for project-local registration or advanced
installer flags:

```bash
pip install adamast
adamast codex install --user-level
```

Both paths install the guidance skill and the same runtime behavior. Do not
enable both paths at once.

> **Use `uv tool install`, not `uvx`.** Hook commands embed the interpreter
> path, and `uvx` resolves to a content-hashed path inside the uv *cache* that
> `uv cache clean` or a version bump invalidates, silently breaking every hook.

### Verify the integration

```bash
claude plugin list                 # native Claude Code plugin
codex plugin list                  # native Codex plugin
adamast doctor                     # package CLI installation
adamast dashboard --trace-output <program-dir>
```

Native plugins keep their managed runtime private and do not modify your
shell's `PATH`. Use the plugin list and the host's `/hooks` view to verify that
path; the `adamast` commands above apply when you installed the package CLI.

The full details (how checkpoints work, the taxonomy picker, background learning, the live monitor, and every knob) live in **[the runtime integration guide](docs/RUNTIME_INTEGRATION.md)**.

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
| `adamast register-taxonomy` | Register an existing taxonomy file into the local store |
| `adamast doctor` | Validate paths, configuration, hooks, and host contracts |
| `adamast status` | Active taxonomy, traces, learning state, recent decisions |
| `adamast dashboard` | Local taxonomy dashboard / checkpoint monitor |
| `adamast traces` | Inspect trace state |
| `adamast claude install` / `uninstall` | Manage Claude Code hooks |
| `adamast claude add-hook` / `remove-hook` / `list-hooks` | Manage custom Claude Code checkpoint hooks |
| `adamast codex install` / `uninstall` | Manage Codex hooks |
| `adamast claude checkpoint` / `adamast codex checkpoint` | Record a private runtime checkpoint (invoked by the hooks) |
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

Development setup, verification commands, and package boundaries: [CONTRIBUTING.md](CONTRIBUTING.md)

The original research pipeline lives on the
[`paper-pipeline`](https://github.com/multi-agent-systems-failure-taxonomy/ATLAS/tree/paper-pipeline)
branch; a maintained, locally patched fork is vendored under
[`adamast/learning/vendor/`](adamast/learning/vendor/) with provenance in
[`VENDORED.md`](adamast/learning/vendor/VENDORED.md).

## 📄 License

Apache-2.0. See [LICENSE](LICENSE).
