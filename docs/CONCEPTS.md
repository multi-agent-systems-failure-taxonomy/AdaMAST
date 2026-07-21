# AdaMAST concepts

This page defines the vocabulary the rest of the documentation uses. Read it
once before the integration guides; every other page assumes these terms.

AdaMAST is not another task solver. It is a runtime supervision layer that gives
an agent a structured way to ask, "what mistake am I about to repeat?" — and a
learning loop that turns completed runs into a failure-mode taxonomy
specialized to your tasks.

## The runtime loop

![AdaMAST runtime loop](adamast_runtime_loop.png)

1. A task starts. AdaMAST selects the active taxonomy: an inherited stored
   taxonomy if one is configured, otherwise the built-in MAST fallback.
2. At configured boundaries (checkpoints, tool failures, subagent stops), the
   agent is asked to reflect on its recent trajectory against the taxonomy.
3. Before the final answer is released, a pre-submission gate requires a
   structured reflection; the agent gets a bounded number of repair attempts.
4. At session end, one canonical trace of the task is recorded.
5. When enough traces accumulate, AdaMAST generates a task-specific taxonomy
   (from MAST warm-up) or refines the active stored taxonomy. Accepted
   results become stored taxonomies that future runs can inherit.

## Key terms

| Term | Meaning |
|---|---|
| Program | One learning stream, identified by its `trace_output` folder. Reusing the same `trace_output` means "same program": counters, pending traces, and the active taxonomy are shared. |
| Taxonomy | A JSON record of failure-mode codes, selected only by `taxonomy_id`. `repo` and `domain` are display metadata and route nothing. |
| Code | One failure mode inside a taxonomy: an id, an observable name, and a task-neutral diagnostic description. |
| Gate / checkpoint | A configured boundary where the agent must reflect before continuing. The final submission gate is the blocking one; others are advisory nudges. |
| Reflection | The required response shape at a gate: Observe → Correlate → Map → Decide. `none apply` is a valid outcome; codes fire only when evidence supports the match. |
| Trace | The canonical record of one completed task (task text, redacted trajectory, metadata). Traces are the input to generation and refinement. |
| Runtime evidence | Per-checkpoint firings and clean reflections recorded while a task runs. This is what the dashboard displays. |
| Generation | The MAST → stored-taxonomy transition. Fires after `generation_threshold` warm-up traces; the candidate must pass the Reflection Judge unless `skip_judge` is set. |
| Refinement | Periodic improvement of an active stored taxonomy, after `k_init` traces first and every `k` traces thereafter. Accepted refinements get a new `taxonomy_id`. |
| Lineage | The successor link from a refined taxonomy to its replacement, so evolution history is preserved across programs. |
| Inheritance | Starting a run from a stored taxonomy via `--inherit <taxonomy_id>` or the interactive picker. No inherit value means "start from MAST". |

## The reflection shape

Every gate asks for the same four steps, in order:

1. **Observe** concrete events or missing expected steps in the recent
   trajectory.
2. **Correlate** only evidence-supported causes.
3. **Map** to taxonomy codes only when the evidence supports the match.
4. **Decide** whether to make one focused repair or continue.

A reflection that maps no codes is a *clean checkpoint* and is recorded as
evidence too. Agents must not invent a failure mode to satisfy the gate.

Decisions that would replace an already-committed answer are additionally
held to a replacement standard: the agent must construct and run a check
that demonstrates the current answer's failure (internal, source,
task-constraint, or completeness consistency) — an alternative's appeal
alone never authorizes a replacement.

## Built-in MAST

MAST is the Multi-Agent System failure Taxonomy from
["Why Do Multi-Agent LLM Systems Fail?" (Cemri et al., 2025)](https://arxiv.org/abs/2503.13657).
AdaMAST ships a 14-code adaptation as its zero-configuration starting point:
when no taxonomy is inherited, runs begin with these codes and MAST warm-up
traces feed the first generation.

| Category | Codes |
|---|---|
| Specification | MAST-1 Disobedient to task specification · MAST-2 Disobedient to role specification · MAST-3 Step repetition · MAST-4 Loss of conversation history · MAST-5 Unaware of termination conditions |
| Coordination | MAST-6 Conversation reset · MAST-7 Failure to ask for clarification · MAST-8 Task derailment · MAST-9 Information withholding · MAST-10 Ignored other agent's input |
| Verification | MAST-11 Premature termination · MAST-12 No or incomplete verification · MAST-13 Weak verification · MAST-14 Incorrect verification |

The full definitions live in [`finding/mast.json`](https://github.com/multi-agent-systems-failure-taxonomy/ATLAS/blob/main/finding/mast.json).
MAST is a built-in constant, not a store record; it does not appear in the
interactive picker.

## What AdaMAST is not

- Not a task solver, planner, or wrapper around your agent's model calls —
  your harness owns model execution.
- Not a static linter: codes fire from evidence in the live trajectory, not
  from source analysis.
- Not a router: `repo` and `domain` never select taxonomies; only
  `taxonomy_id` does.

## Where to go next

- First run: [GETTING_STARTED.md](GETTING_STARTED.md)
- Every config field: [CONFIGURATION.md](CONFIGURATION.md)
- The learning lifecycle in detail: [TRACES_AND_LEARNING.md](TRACES_AND_LEARNING.md)
- Writing your own harness: [INTEGRATION.md](INTEGRATION.md)
