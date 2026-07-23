# Choose an AdaMAST workflow

This page does two things: routes you to the right guide for what you need,
and defines the handful of words (taxonomy, trace, checkpoint, judge) that
every other page uses. AdaMAST is a set of composable taxonomy workflows.

## 🗺️ Workflow map

Choose by the decision you need to make and by who owns the trace lifecycle:

| Need | Persistent state? | Model calls | Guide |
| --- | --- | --- | --- |
| Check or convert trace files | No | None | [Prepare traces](TRACE_FORMATS.md) |
| Build a taxonomy from a fixed trace set | No | Draft + four-annotator agreement | [Generate a taxonomy](GENERATION.md) |
| Inspect one generated taxonomy | No | None | [Outputs and field guide](TAXONOMY_OUTPUTS.md) |
| Label new traces with supported failure codes | No | One judge call per trace | [Judge traces](JUDGING.md) |
| Analyze causality, coverage, quality, or calibration | Optional | Depends on judge type | [Choose a judge](JUDGE_TYPES.md) |
| Generate/refine as traces accumulate | Yes | Threshold-triggered learning calls | [Adaptive runtime](GETTING_STARTED.md) |
| Add runtime checkpoints to a custom program | Yes | Harness-defined | [Custom agent harness](INTEGRATION.md) |
| Add runtime checkpoints to Codex or Claude Code | Yes | Native host workers or configured provider | [Host integrations](CODEX.md) |

## 🏷️ Taxonomy

A taxonomy is a catalog of observable failure modes, each entry backed by
verbatim evidence from real traces. Generated output is a flat `codes`
list in `taxonomy.json`.

AdaMAST uses three code categories:

| Category | Meaning |
| --- | --- |
| A | System or execution failures that can affect any role |
| B | Role-specific quality failures |
| C | Domain reasoning or cross-role failures |

## 🧾 Trace

A trace is the evidence from one completed task or episode: a stable ID, the
task, the raw trajectory, and optional metadata. Common agent log formats
are accepted and normalized; see [Prepare traces](TRACE_FORMATS.md).

## ✍️ Generation

Generation proposes a new taxonomy from a set of traces, on demand: point
`adamast generate` at your files and the annotators do the rest.

## 🤝 Agreement

The agreement layer asks four independent annotators to discover,
reconcile, type, and code failures. The taxonomy is accepted only if final
macro Fleiss kappa and coverage meet their configured targets.

## ⚖️ Judge

A judge applies or evaluates a taxonomy. The default judge selects every
failure code a trace's evidence supports; a single-code mode returns one
best code instead. Specialized judges can map a failure point, measure
coverage, review codebook quality, calibrate an annotation, or build a
causal reflection graph.

---

## 🔌 Runtime-integration concepts

!!! note "Only for the live runtime"
    Everything below matters only when AdaMAST runs **live** inside Codex,
    Claude Code, or a custom harness. If you generate and judge from trace
    files, you can stop reading here.

## 🧮 Program and runtime state

A runtime **program** is one learning stream identified by its
`trace_output` folder. It owns pending traces, the active taxonomy,
generation/refinement counters, and local status. Codex and Claude Code give
each conversation its own **conversation branch**; custom harnesses opt into
sharing only by reusing a path.

## 🚧 Checkpoint (gate)

A **checkpoint**, also called a **gate** (the two words name the same event,
and these docs standardize on *checkpoint*), is a runtime boundary where the
agent reflects on recent evidence using the active taxonomy. Most checkpoints
are advisory; the **final gate**, the pre-submission checkpoint, can block
until the required response shape passes or the retry policy is exhausted.

The runtime reflection shape is:

1. **Observe** concrete activity or a missing expected step.
2. **Correlate** only evidence-supported causes.
3. **Map** supported evidence to taxonomy codes.
4. **Decide** whether to repair or continue.

!!! note
    `none apply` is a valid clean checkpoint. Finding nothing wrong is an
    answer, not a skipped step.

## 🌱 The starting taxonomy

When the runtime integration starts working in a project that has no learned
taxonomy yet, checkpoints still need a catalog to check against from the very
first message. For that period the runtime uses a static, pre-made taxonomy,
selected once at conversation start, until enough traces accumulate to
generate a custom one for your project.

We selected [MAST](https://arxiv.org/abs/2503.13657) for this role: a
general-purpose 14-code taxonomy of common agent failure modes from *"Why Do
Multi-Agent LLM Systems Fail?"* (Cemri et al., 2025), which AdaMAST ships in
an adapted form. The traces completed while MAST is active feed the first
taxonomy generated for your own project; standalone generation never needs
it.

## 🔀 Refinement and lineage

Refinement reviews one branch's active stored taxonomy after that
conversation accumulates additional traces. An accepted change receives a
new `taxonomy_id`; the predecessor and child remain connected by lineage.
Several branches may create different children from the same parent, so the
current taxonomy is resolved from the branch head rather than a global
latest pointer. A valid `no_change` review advances that branch's cadence
without replacing it.

## ➡️ Next step

Continue with [Prepare traces](TRACE_FORMATS.md) unless you already have a
validated AdaMAST source. Move down the numbered sidebar levels only when
the preceding workflow is insufficient for your use case.
