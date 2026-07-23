# Adaptive runtime overview

Use the adaptive runtime when AdaMAST must do more than process a fixed trace
dataset. A runtime integration keeps a taxonomy active during tasks, records
completed traces, and starts generation or refinement as new evidence
accumulates.

Complete the standalone [Foundation](BASELINE_GENERATION.md) and
[Evaluation](JUDGING.md) guides first if you only need a fixed taxonomy and a
judge.

## What changes at runtime

| Standalone workflow | Adaptive runtime |
| --- | --- |
| You provide a finished trace dataset | The integration records one canonical trace per completed task or episode |
| Generation runs when you invoke it | Generation/refinement runs when configured trace thresholds are reached |
| `taxonomy.json` is a file you select | A program tracks its active taxonomy and successor lineage |
| Judging is a separate batch call | Checkpoints and the final gate can apply taxonomy guidance during work |
| No persistent counters | Pending traces, generation state, and refinement counters persist |

## Install and configure

Install the package as described on the
[documentation home](index.md#install-adamast), then create `adamast.json` in
the project using the runtime:

```json
{
  "version": 1,
  "trace_output": "./adamast-program",
  "adamast_model": "gpt-5"
}
```

`trace_output` identifies one learning stream. Reusing it means the active
taxonomy, pending traces, and counters are shared. Use a different path when
two task streams must learn independently.

Codex and Claude Code perform this separation automatically: every new
interactive conversation is routed to a conversation-owned program branch
beneath the project root. Custom harnesses must choose their own
`trace_output` boundary.

`adamast_model` is the model used for generation, judging, and refinement. It
is separate from the model used by the task-solving agent.

## Verify runtime configuration

```bash
adamast-doctor --config adamast.json
```

Relative paths are resolved from the config file. Unknown fields fail loudly.
See the [configuration reference](CONFIGURATION.md) for every threshold,
storage path, learning mode, and gate option.

## Choose an integration surface

### Single model call or batch program

Use the [single-LLM integration](SINGLE_LLM.md) when a script, notebook,
benchmark runner, or batch job owns the model call and can define task
boundaries explicitly.

### Custom agent harness

Use the [custom harness guide](INTEGRATION.md) when an application owns agent
events, tool boundaries, and transcript capture. The harness calls the runtime
API at session start, meaningful checkpoints, final submission, and trace
commit.

### Codex or Claude Code

Use a host installer only after choosing the runtime behavior you want. The
[Codex](CODEX.md) and [Claude Code](CLAUDE_CODE.md) guides cover only their
host-specific hooks, selector behavior, event contracts, and uninstall steps.

## Minimal runtime lifecycle

```python
from adamast import (
    GenerationTrace,
    end_session,
    pre_submission,
    record_trace,
    start_session,
)

session = start_session(
    trace_output="./adamast-program",
    adamast_model="gpt-5",
)

# Deliver session.delivery.runtime_protocol at task start.
# Invoke checkpoint handling at boundaries owned by your harness.

decision = pre_submission(session, gate_text)
if not decision.allow:
    # Ask the agent to repair or re-emit the required gate response.
    pass

record_trace(
    session,
    GenerationTrace(
        problem_id="task-17",
        task="original task",
        raw_trajectory="complete redacted trajectory",
        metadata={"harness": "my-pipeline"},
    ),
)

result = end_session(session)
```

`end_session()` checks learning thresholds. It may start generation when MAST
is active or refinement when a stored taxonomy is active.

## Default learning cadence

| Transition | Default threshold |
| --- | ---: |
| MAST warm-up to first generated taxonomy | `generation_threshold = 5` traces |
| First refinement after activation | `k_init = 10` new traces |
| Later refinement reviews | `k = 20` new traces |

The active taxonomy remains stable while a worker runs. A generated or refined
candidate must pass its configured validation before activation. Rejected
candidates preserve their input traces for later review.

## Taxonomy selection

| Input | Runtime behavior |
| --- | --- |
| no inherit value | Start from built-in MAST |
| explicit `taxonomy_id` | Start from that stored taxonomy |
| explicit picker request | Open the local taxonomy selector |
| `No taxonomy` in an interactive host | Disable AdaMAST for that conversation |

Repository and domain fields are display metadata; they never route taxonomy
selection.

## Privacy boundary

The runtime stores traces and can send trace excerpts to the configured
AdaMAST model. Redact credentials, cookies, private data, sensitive paths, and
benchmark oracle information before calling `record_trace()`.

Bundled adapters include conservative redaction, but a custom harness remains
responsible for domain-specific secrets.

## Continue by responsibility

- [Traces and learning](TRACES_AND_LEARNING.md): generation/refinement timing,
  counters, freeze mode, retention, and evidence exports.
- [Taxonomy lifecycle](TAXONOMIES.md): records, IDs, inheritance, activation,
  and lineage.
- [Custom agent harness](INTEGRATION.md): complete ownership boundary and call
  sequence.
- [Native taxonomy learning](NATIVE_LEARNING.md): native Codex/Claude worker
  protocol.
