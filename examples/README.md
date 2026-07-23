# examples/

Runnable demonstration scripts. Each example stands on its own — no
project glue, no test harness, no implicit imports beyond the standard
adamast packages. Safe to copy out as a starting point for a new
integration.

## Programs

| File | Purpose |
|---|---|
| [`__init__.py`](__init__.py) | Package marker |
| [`dashboard_demo.py`](dashboard_demo.py) | Launch a disposable taxonomy dashboard populated with placeholder evidence — no LLM calls. Useful for poking the UI |
| [`manual_vendored_generation.py`](manual_vendored_generation.py) | MANUAL: run the real vendored induction pipeline with configured API credentials. Costs money — read before running |
