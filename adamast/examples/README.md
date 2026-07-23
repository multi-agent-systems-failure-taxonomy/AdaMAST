# adamast/examples/

Runnable demonstration scripts, shipped inside the package so they work
from a pip install as well as a source checkout. Each example stands on
its own: no project glue, no test harness, no implicit imports beyond
the standard adamast packages. Safe to copy out as a starting point for
a new integration.

Run `python -m adamast.examples` to copy the bundled files into
`./adamast-examples/`, or run a script directly, for example
`python -m adamast.examples.dashboard_demo`.

## Programs

| File | Purpose |
|---|---|
| [`__init__.py`](__init__.py) | Package marker |
| [`dashboard_demo.py`](dashboard_demo.py) | Launch a disposable taxonomy dashboard populated with placeholder evidence, no LLM calls. Useful for poking the UI |
| [`judge_usage.py`](judge_usage.py) | Apply one accepted taxonomy to new traces via `create_judge` / `load_traces`. Needs a provider credential |
| [`manual_vendored_generation.py`](manual_vendored_generation.py) | MANUAL: run the real vendored induction pipeline with configured API credentials. Costs money; read before running |
