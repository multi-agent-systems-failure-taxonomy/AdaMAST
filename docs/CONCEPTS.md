# Choose an AdaMAST workflow

AdaMAST is a set of composable taxonomy workflows, not only a Codex or Claude
Code integration. Choose by the decision you need to make and by who owns the
trace lifecycle.

## Workflow map

| Need | Persistent state? | Model calls | Guide |
| --- | --- | --- | --- |
| Check or convert trace files | No | None | [Prepare traces](TRACE_FORMATS.md) |
| Build a taxonomy from a fixed trace set | No | Draft + four-annotator agreement | [Generate a taxonomy](BASELINE_GENERATION.md) |
| Inspect one generated taxonomy | No | None | [Outputs and field guide](TAXONOMY_OUTPUTS.md) |
| Label new traces with one best code | No | One judge call per trace | [Judge traces](JUDGING.md) |
| Analyze causality, coverage, quality, or calibration | Optional | Depends on judge type | [Choose a judge](JUDGE_TYPES.md) |
| Generate/refine as traces accumulate | Yes | Threshold-triggered learning calls | [Adaptive runtime](GETTING_STARTED.md) |
| Add runtime gates to a custom program | Yes | Harness-defined | [Custom agent harness](INTEGRATION.md) |
| Add runtime gates to Codex or Claude Code | Yes | Native host workers or configured provider | [Host integrations](CODEX.md) |

## Taxonomy

A taxonomy is a versioned catalog of observable failure modes. Public BASELINE
output uses a flat `codes` list. Runtime storage adds an immutable
`taxonomy_id`, activation state, and successor lineage.

AdaMAST uses three code categories:

| Category | Meaning |
| --- | --- |
| A | System or execution failures that can affect any role |
| B | Role-specific quality failures |
| C | Domain reasoning or cross-role failures |

## Trace

A trace is the evidence from one completed task or episode: a stable ID, the
task, the raw trajectory, and optional metadata. Standalone workflows read
files. Runtime integrations create the same conceptual record at an accepted
task boundary.

## Generation

Generation proposes a new taxonomy from traces. In BASELINE it runs on demand.
In the adaptive runtime it runs after a program reaches its warm-up threshold.
These strategies need not remain identical: the shared trace and taxonomy
contracts leave room for additional generation methods.

## Agreement

The BASELINE agreement layer asks four independent annotators to discover,
reconcile, type, and code failures. The taxonomy is accepted only if final macro
Fleiss kappa and coverage meet their configured targets.

## Judge

A judge applies or evaluates a taxonomy. The core judge chooses one best code
for a trace. Specialized judges can select multiple codes, map a failure point,
measure coverage, review codebook quality, calibrate an annotation, or build a
causal reflection graph.

## Program and runtime state

A runtime **program** is one learning stream identified by its `trace_output`
folder. It owns pending traces, the active taxonomy, generation/refinement
counters, and local status. Reusing the path means reusing that state.

## Gate and checkpoint

A gate is a runtime boundary where the agent reflects on recent evidence using
the active taxonomy. Checkpoints are usually advisory; the final submission
gate can block until the required response shape passes or the retry policy is
exhausted.

The runtime reflection shape is:

1. **Observe** concrete activity or a missing expected step.
2. **Correlate** only evidence-supported causes.
3. **Map** supported evidence to taxonomy codes.
4. **Decide** whether to repair or continue.

`none apply` is a valid clean checkpoint.

## MAST warm-up

When no stored taxonomy is inherited, the runtime can begin with the built-in
14-code MAST adaptation. Completed warm-up traces support the first generated
project taxonomy. MAST is a runtime seed, not a requirement for standalone
BASELINE generation.

## Refinement and lineage

Refinement reviews an active stored taxonomy after additional traces
accumulate. An accepted change receives a new `taxonomy_id`; the predecessor
and successor remain connected by lineage. A valid `no_change` review advances
the cadence without replacing the taxonomy.

## Next step

Start with [Prepare traces](TRACE_FORMATS.md) unless you already have a
validated AdaMAST source. Move down the numbered sidebar levels only when the
preceding workflow is insufficient for your use case.
