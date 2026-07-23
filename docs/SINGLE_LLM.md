# Single-LLM integration

Use this path when your application owns the model call: scripts, notebooks, benchmarks, batch jobs, or custom pipelines.

## CLI wrapper

```bash
adamast-single-run \
  --config adamast.json \
  --task "Solve the task, then pass through AdaMAST before final answer." \
  --model gpt-5 \
  --gate-exhaustion-policy release \
  --recent-activity-messages 8 \
  --recent-activity-chars 12000
```

The `--model` flag is the task-solving model. The `adamast_model` field in `adamast.json` is the AdaMAST generation, judge, and refinement model.

`gate_exhaustion_policy` controls what happens when the final gate still
blocks after the retry cap:

- `raise` keeps the strict default and exits with an error.
- `release` returns the best available answer and records `gate_allowed=false`.

The recent-activity limits bound checkpoint/final-gate prompt growth while
preserving the original task prompt and a tail of recent messages.

## Programmatic integration

Custom programs can call the runtime directly. A minimal adapter looks like this:

```python
from adamast import GenerationTrace, end_session, load_adamast_config, record_trace, start_session

config = load_adamast_config("adamast.json")
session_args = dict(
    trace_output=config["trace_output"],
    store_dir=config.get("store_dir", "~/.adamast/taxonomies"),
    trace_root=config.get("trace_root", "~/.adamast/traces"),
    adamast_model=config.get("adamast_model"),
    generation_threshold=config.get("generation_threshold", 5),
    generation_stops=config.get("generation_stops", False),
    k_init=config.get("k_init", 10),
    k=config.get("k", 20),
    refinement_stops=config.get("refinement_stops", False),
    advanced_refinement=config.get("advanced_refinement", False),
    freeze=config.get("freeze", False),
    dashboard=config.get("dashboard", True),
)
if config.get("inherit") is not None:
    session_args["inherit"] = config["inherit"]

session = start_session(**session_args)

try:
    # Run your own task-solving model here, then save the canonical AdaMAST trace.
    answer = run_my_model(...)
    record_trace(
        session,
        GenerationTrace(
            problem_id="UID0118",
            task="original task text",
            raw_trajectory="model-visible trajectory and final answer",
            metadata={"answer": answer},
        ),
    )
finally:
    end_session(session)
```

The exact method names depend on the adapter layer you choose, but the contract is stable:

1. start a session with a mandatory trace output;
2. resolve the active taxonomy;
3. call checkpoint/final gates at meaningful boundaries;
4. record one canonical trace at the end.

## When to use this path

Use the single-LLM path when:

- there is no agent harness with hooks;
- each dataset row or benchmark sample is a separate task;
- you want deterministic control over when AdaMAST is invoked;
- you want to compare AdaMAST-on and AdaMAST-off runs from the same script.

See [API_OR_RUNTIME.md](API_OR_RUNTIME.md) for lower-level runtime notes.
