# adamast/hosts/single_llm/

Harness-free AdaMAST integration. Drives one LLM agent through the runtime
lifecycle without any host framework: useful for scripts, notebooks, smoke
tests, and as the reference adapter for new integrations.

## Programs

| File | Purpose |
|---|---|
| [`__init__.py`](__init__.py) | Exports `SingleLLMConfig`, `SingleLLMResult`, `run_single_llm` |
| [`cli.py`](cli.py) | `adamast single-run` CLI entry point: read task from stdin/args, drive one run, print result JSON |
| [`runtime.py`](runtime.py) | `run_single_llm` orchestration: build messages, call the user's LLM callback per turn, record canonical traces, run pre-submission gate, finish the session |
