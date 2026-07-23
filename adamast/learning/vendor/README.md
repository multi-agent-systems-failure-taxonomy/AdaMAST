# adamast/learning/vendor/

Vendored snapshot of the upstream AdaMAST package
(`multi-agent-systems-failure-taxonomy/ATLAS`) — a research toolkit that
induces a 3-axis failure taxonomy (A / B / C) from agent execution traces
and classifies new traces against an existing taxonomy.

AdaMAST calls this layer through `adamast/generation.py::_adamast_generate`
(for `generate_taxonomy`) and via `adamast.learning.vendor.api.generate_taxonomy`
directly from `adamast/import_generation.py`. The vendored copy is
treated as read-only; see [`../README.md`](../README.md) for refresh notes.

## Programs

| File | Purpose |
|---|---|
| [`__init__.py`](__init__.py) | Public API re-exports |
| [`__main__.py`](__main__.py) | Allows `python -m adamast.learning.vendor` to dispatch to the CLI |
| [`api.py`](api.py) | High-level entry points: `generate_taxonomy`, `classify_trace`, `classify_traces` |
| [`classifier.py`](classifier.py) | `TaxonomyClassifier` + `Diagnosis` dataclass — single-trace classification against a known taxonomy |
| [`cli.py`](cli.py) | Command-line interface: `generate` and `classify` subcommands |
| [`config.py`](config.py) | `PipelineConfig` (model, max_codes, timeout, sampling); env-driven model defaults |
| [`llm.py`](llm.py) | Unified LLM client wrapper for OpenAI- and Anthropic-compatible backends |
| [`utils.py`](utils.py) | Small shared helpers used across pipeline stages |

## Sub-folders

- [`pipeline/`](pipeline/) — The 8-step taxonomy generation pipeline
  (domain → structure → category generation → dedup → validation → final
  check). Driven by `TaxonomyPipeline.run`.

- [`traces/`](traces/) — Trace loading + normalization + behavioral signal
  extraction. Handles ~8 auto-detected input formats and converts them to
  the unified AdaMAST trace schema before generation/classification.
