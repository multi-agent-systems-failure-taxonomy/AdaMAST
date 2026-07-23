# tests/

Unit + integration tests for the adamast package. Run the suite with
`python -m pytest tests/` from the repo root. Tests use hand-crafted
fixtures (under [`fixtures/`](fixtures/)) — no live LLM calls.

## Test files

| File | Covers |
|---|---|
| [`__init__.py`](__init__.py) | Test-package marker + shared defaults |
| [`test_claude_code_integration.py`](test_claude_code_integration.py) | Claude Code runtime skin: hooks, install/uninstall, config round-trip, transcript handling |
| [`test_cli.py`](test_cli.py) | `adamast-find` CLI: stdout/exit-code wiring for inherit-by-id, explicit picker, deprecated bare-picker, and missing-id errors |
| [`test_config.py`](test_config.py) | Shared `adamast.json` config loading, validation, precedence, and CLI wiring |
| [`test_dashboard.py`](test_dashboard.py) | Persistent live taxonomy dashboard (HTTP server, refresh, stop semantics) |
| [`test_doctor.py`](test_doctor.py) | `adamast-doctor` health checks for storage, model recognition, JSON output, and error status |
| [`test_generation_lifecycle.py`](test_generation_lifecycle.py) | MAST → generated-taxonomy lifecycle: warm-up threshold, blocking vs background, rejection paths |
| [`test_import_generation.py`](test_import_generation.py) | `adamast-import-traces` flow: trace loading, refinement-based registration, atomic rollback on failure |
| [`test_judge_types.py`](test_judge_types.py) | `adamast/judges/` registry, natural-language simple-judge asset loading, selection-summary bucket completeness, Reflection Judge construction |
| [`test_learning_calls.py`](test_learning_calls.py) | LLM-transport boundaries (Anthropic / OpenAI / Gemini), JSON repair-retry, prompt formatters |
| [`test_lifecycle.py`](test_lifecycle.py) | Agent- and model-agnostic lifecycle (start/record/pre_submission/end), idempotency, error paths |
| [`test_mast.py`](test_mast.py) | Built-in MAST floor: 14 known modes, category mapping, fixture-immutability |
| [`test_protocol.py`](test_protocol.py) | Pre-submission gate: reflection shape validation + repair-retry envelope |
| [`test_refinement_lifecycle.py`](test_refinement_lifecycle.py) | Program-local refinement counters + global taxonomy lineage successor links |
| [`test_redaction.py`](test_redaction.py) | Public trace redaction helpers for common secrets and project-specific patterns |
| [`test_repository.py`](test_repository.py) | Display-only repository metadata discovery (git remote, repo name) |
| [`test_resolver.py`](test_resolver.py) | Resolver: three `--inherit` behaviors plus none-selection |
| [`test_runtime_options.py`](test_runtime_options.py) | Reusable runtime CLI options (`RuntimeOptions`, `parse_runtime_args`) |
| [`test_single_llm_integration.py`](test_single_llm_integration.py) | Single-LLM no-harness runtime (drives a stubbed model through the lifecycle) |
| [`test_skip_judge.py`](test_skip_judge.py) | `--skip-judge` flag plumbing: defaults, override path, refinement bypass, session round-trip |
| [`test_store.py`](test_store.py) | Flat taxonomy store: register / fetch_by_id / list_all / unregister, schema validation, atomic writes |
| [`test_taxonomy_data.py`](test_taxonomy_data.py) | Taxonomy data-model helpers: round-trips, mutations, retirement bookkeeping |
| [`test_traces.py`](test_traces.py) | Generation-trace storage + retention reports |
| [`test_traces_cli.py`](test_traces_cli.py) | `adamast-traces` status/export/prune behavior, including dry-run pruning |
| [`test_webview.py`](test_webview.py) | Webview HTTP server: table rendering, detail view, choice recording (no browser needed) |
| [`test_adapter_contracts.py`](test_adapter_contracts.py) | Shared adapter prompt, checkpoint, and lifecycle contracts |
| [`test_claude_native_learning.py`](test_claude_native_learning.py) | Claude native subagent claims, UTF-8 receipts, polling, and activation |
| [`test_codex_integration.py`](test_codex_integration.py) | Codex hooks, selector recovery, compact checkpoints, traces, and install/uninstall |
| [`test_codex_learning_jobs.py`](test_codex_learning_jobs.py) | Native Codex learning jobs, receipts, evidence validation, and activation |
| [`test_conversation_scope.py`](test_conversation_scope.py) | Durable host-conversation routing across cwd changes and resumes |
| [`test_custom_hooks.py`](test_custom_hooks.py) | Claude custom-hook dispatch and evidence recording |
| [`test_fsio.py`](test_fsio.py) | Retried file reads and atomic-write behavior |
| [`test_judge_implementations.py`](test_judge_implementations.py) | Concrete judge behavior and malformed-output handling |
| [`test_project_scope.py`](test_project_scope.py) | Project-key discovery and task-group path isolation |
| [`test_refiner_no_retire.py`](test_refiner_no_retire.py) | Refiner constraints around stable and retired codes |
| [`test_reflection_parser.py`](test_reflection_parser.py) | Markdown-tolerant reflection parsing and format repair |
| [`test_register_taxonomy.py`](test_register_taxonomy.py) | Existing-taxonomy registration CLI and validation |
| [`test_runtime_robustness.py`](test_runtime_robustness.py) | Manifest, worker, and persistence recovery paths |
| [`test_status.py`](test_status.py) | Program status output and health fields |

## Sub-folders

- [`fixtures/`](fixtures/) — Shared test fixtures: a real AdaMAST generation
  trace, real AdaMAST generation output, and the example taxonomies
  (`tax-django-orm-001` etc.) used by store/resolver/webview tests.
