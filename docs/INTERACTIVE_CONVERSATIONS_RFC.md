# Interactive conversations runtime RFC

Status: Implemented for Codex and Claude Code in AdaMAST 1.1.0b4. This document
retains the design rationale and records remaining management-surface work.

This RFC adds a conversation-oriented runtime without changing the existing
batch and harness APIs. Batch callers continue to treat one launched task as
one trace. Codex and Claude Code conversations instead treat each completed
user turn as one learning episode.

## Current behavior and gaps

The shared runtime is task-shaped:

1. `start_session()` selects a taxonomy and registers an active task.
2. The harness records one or more traces in memory.
3. `end_session()` persists the traces, closes the task, and evaluates the
   generation or refinement threshold.

That shape is correct for benchmark tasks and terminal-launched jobs. Before
this branch, both conversation adapters mapped an entire harness session to
that task shape. They used the first user message as the task and saved the
complete transcript once. Later user turns in the same conversation did not
become independent traces.

Other current limitations are:

- User-level Codex and Claude Code installers use the same default
  `~/.adamast/interactive` root so both hosts share a project program.
  Explicit project configs can still choose separate roots.
- Taxonomy inheritance is explicit or program-local; there is no semantic
  automatic selector for a new task group.
- Native refinement review is count-triggered, though a review may now return
  `no_change` when evidence does not justify a successor.
- Automatic secret redaction and per-turn learning exclusion are default-on.
- Unified task-group selection, rollback controls, and dashboard management
  remain pending.

## Decisions

### One completed user turn is one episode trace

An interactive episode begins with a substantive user prompt and ends at the
main agent's Stop boundary. Claude Code can pass the full blocking reflection
gate. Codex validates the compact final checkpoint in the original answer and
commits in one callback because observed desktop builds can complete a hook
continuation without redelivering Stop. Its trace contains the transcript delta
since the previous committed Stop, including tool calls, failures, repairs, and
the final checkpoint.

A follow-up request is a new episode even when it stays in the same harness
conversation. `TaskCompleted` and `SubagentStop` remain checkpoints and
evidence sources; they do not create additional learning traces by default.

Do not count:

- AdaMAST control commands;
- a prompt rejected before the agent runs;
- an empty session that exits before a user turn;
- duplicate Stop delivery for an already committed episode.

Every episode has a stable identity and metadata:

```text
<harness>:<conversation-id>:episode:<sequence>
```

```json
{
  "harness": "codex",
  "conversation_id": "...",
  "episode_sequence": 4,
  "trace_granularity": "episode",
  "taxonomy_id": "tax-company-tools-1",
  "runtime_session_id": "codex:...:episode:4"
}
```

Each episode pins one taxonomy version. A conversation pins a taxonomy
lineage, not necessarily one immutable version. An accepted successor may be
used at the next episode boundary, never halfway through an answer.

### Batch behavior stays unchanged

`single_llm` and direct lifecycle callers keep their current task-level trace
contract. Episode segmentation belongs to interactive adapters. This avoids
silently changing benchmark denominators or existing terminal automation.

### Project and task-group scope

The default sharing hierarchy is:

```text
global taxonomy store
  -> project
    -> task group (default: "default")
      -> conversation lineage lease
        -> immutable episode version
```

Tasks in the same project folder share the default task group's program,
traces, active taxonomy, and refinement counters across Codex and Claude Code.
A project with genuinely different work can create named groups such as
`frontend`, `billing`, or `support-automation`.

Project identity resolution order:

1. explicit `project_id` configuration;
2. canonical, resolved Git top-level path;
3. canonical workspace path.

The runtime stores project data outside the repository by default:

```text
~/.adamast/projects/<project-key>/groups/<group-id>/
  program/
  conversations/
  jobs/
```

An explicit `trace_output` remains supported and wins for batch compatibility.
Git worktrees are separate projects by default; an explicit `project_id` can
join them.

### Taxonomy selection and carry-over

Recommended default selection order for a new conversation:

1. explicit conversation override;
2. active taxonomy for the selected project group;
3. MAST.

`auto` selection is opt-in. It may compare the first substantive task or an
explicit group description with taxonomy domains and code summaries. Repository
metadata remains display-only and must not influence selection. If confidence
is insufficient, selection returns MAST.

The control surface should support:

```text
adamast context status
adamast context group use billing
adamast context use auto --scope conversation
adamast context use mast --scope conversation
adamast context use tax-company-tools-1 --scope conversation
adamast context use tax-company-tools-1 --scope group
adamast context lock tax-company-tools-1
adamast context follow-latest
adamast learning refine --now
adamast learning pause
adamast learning resume
```

In an app, a user may express the same command as a dedicated first control
turn, for example, `AdaMAST: use tax-company-tools-1 for this conversation`.
Control turns are acknowledged but excluded from learning traces. The
SessionStart hook injects an opaque conversation token so the agent can call
the CLI without guessing which concurrent conversation to update.

An exact version lock never follows refinements. The normal conversation lease
follows accepted successors at the next episode boundary.

### Learning drivers

Generation and refinement are runtime decisions. The runtime owns thresholds,
frozen trace snapshots, single-flight locks, validation, and activation. A
worker may only produce a staged candidate.

Supported driver contract:

| Driver | Intended use | External API key |
|---|---|---|
| `native_subagent` | Interactive Codex or Claude Code | No |
| `external_api` | Unattended and batch runs | Yes |
| `authenticated_cli` | Optional unattended fallback using an installed, signed-in harness CLI | No separate API key |
| `disabled` | Trace and gate only | No |
| `auto` | Choose a supported driver explicitly and report the choice | Depends |

`auto` should prefer a native subagent in an interactive harness, then an
authenticated CLI worker when explicitly allowed, then an external provider
when its credential is available. If none is usable, the job remains queued
and the user sees a waiting notice. It must not fail silently.

Native subagent operation consumes the harness account's model allowance. It
does not make taxonomy learning free; it removes the requirement for a second
API credential.

### Native taxonomy worker

The worker is a dedicated `adamast-taxonomy-worker` agent. It receives only:

- the job manifest;
- a frozen, redacted, outcome-blind trace snapshot;
- the active taxonomy for refinement jobs;
- the generation/refinement output schema;
- a read-only job directory.

It must not activate a taxonomy or write directly to the global taxonomy
store. In Codex, it returns a bounded candidate receipt in its final message;
`SubagentStop` captures the receipt into durable state. The runtime then runs
structural validation, taxonomy acceptance, and transactional activation.

Job states:

```text
queued -> claimed -> awaiting_reconcile -> activating
       -> activated | no_change
       -> rejected | failed
```

Only one generation or refinement job may run for a project group. A job uses
a frozen trace list and remains idempotent by `job_id`. A stale claim is
recoverable after a lease timeout.

Codex and Claude Code each queue a durable claim and ask the active task to
launch one native subagent for the current phase against a frozen prompt and
strict output schema. A replacement proposal is followed by a separately
claimed support-review phase. Each subagent submits a signed receipt through
`SubagentStop`; neither receives taxonomy-store activation authority or needs a
standalone CLI. The foreground hook coordinator owns validation and activation.

### Generation and refinement policy

For an interactive demo, keep generation responsive:

```text
generation threshold: 5 eligible episodes
first refinement review: 10 eligible episodes
later refinement reviews: every 20 eligible episodes
```

Generation creates a first project-specific taxonomy from MAST warm-up traces.
The worker starts after the triggering episode is committed. The current
taxonomy remains active while it runs.

Interactive refinement should default to `adaptive`, not unconditional. At a
threshold, the worker first decides whether the new evidence supports a real
change. Evidence may justify refinement when repeated failures are poorly
covered, codes are persistently ambiguous or overlapping, or user corrections
reveal a missing distinction. Cleanly mapped traces may yield
`reviewed_no_change`; no successor is created, and the next review threshold is
scheduled normally.

Available policies:

- `adaptive`: threshold opens a review; a successor is optional.
- `threshold`: preserve the current always-propose behavior.
- `manual`: refine only after an explicit command.
- `off`: never refine, while still recording traces and evidence.

Refinement lineage is global audit data, but activation is project-group local.
Other groups using the same taxonomy do not automatically follow a successor
published elsewhere unless their follow policy allows it. The publishing group
and its unlocked conversations follow the accepted successor at the next
episode boundary.

### Gate and learning notices

The existing compact milestone format stays:

```text
Checkpoint: investigation complete
Relevant codes: MAST-1, MAST-4
Evidence: The repository and requested contract were inspected.
Next action: Implement the focused milestone.
```

Claude Code's blocking reflection remains machine-parseable internally. Codex
uses the four-line checkpoint as its machine-readable one-pass gate and keeps
long reflection internal. The user-facing learning notices use one
cross-harness vocabulary:

```text
AdaMAST: Episode 5 accepted; trace saved; generation threshold reached.
AdaMAST: Taxonomy generation queued; worker adamast-taxonomy-worker; MAST remains active.
AdaMAST: Taxonomy generation running in the background.
AdaMAST: Taxonomy generation completed; tax-company-tools-1 activates next turn.
AdaMAST: Refinement review queued for tax-company-tools-1.
AdaMAST: Refinement completed; tax-company-tools-1 -> tax-company-tools-2 activates next turn.
AdaMAST: Refinement reviewed; no taxonomy change was justified.
AdaMAST: Learning failed; the current taxonomy remains active; traces were preserved.
```

Codex should emit these through hook `systemMessage` output. Claude Code should
use visible hook output for the operator and `additionalContext` for the agent.
Receipts are consumed exactly once per conversation, but remain in the project
audit log.

For Codex, a lifecycle notice is consumed only on a hook that can affect model
context: `SessionStart` or `UserPromptSubmit`. `PostToolUse` remains a silent
poll because Codex does not document `additionalContext` delivery for that
event. Terminal `Stop` and `SubagentStop` events may queue or reconcile the
notice but must leave it durable for the next model-context event.

Learning never blocks ordinary task progress by default. The final quality gate
may still block for a real repair. A user may opt into blocking learning for
controlled evaluations.

## Additional requirements

The original checklist also needs these production requirements:

- **Privacy and redaction:** redact secrets before persistence and before a
  worker snapshot; support per-turn exclusion and project-wide learning pause.
- **Canonical normalization:** Codex now persists a bounded normalized JSONL
  view that excludes harness context; a fully shared mixed-harness schema is
  still pending.
- **Concurrency:** lock project-group state and make episode commit, job claim,
  receipt consumption, and activation idempotent.
- **Crash recovery:** recover open episodes, stale workers, app exits,
  compaction, resume, and partially written candidates.
- **Provenance:** record trace references, harness, worker driver/model,
  configuration version, validation result, and parent taxonomy.
- **Rollback:** preserve immutable taxonomy versions and allow a project group
  to repoint to a previous version without deleting lineage.
- **Retention and deletion:** expose trace inventory, export, exclusion, and
  explicit deletion; never silently expire evidence used for activation.
- **Cost and quota reporting:** distinguish provider API usage from native
  harness usage, which may not expose token or dollar metadata.
- **Migration:** retain support for existing session-level traces and explicit
  `trace_output` programs.
- **Acceptance policy:** allow automatic activation for a demo and optional
  human approval for production environments.

## Codex configuration

The implemented Codex configuration is:

```json
{
  "version": 1,
  "trace_output": "~/.adamast/interactive",
  "generation_threshold": 5,
  "k_init": 10,
  "k": 20,
  "freeze": false,
  "codex": {
    "project_scope": "auto",
    "task_group": "default",
    "session_selector": "prompt",
    "learning_backend": "codex_subagent",
    "worker_timeout_seconds": 1800
  }
}
```

## Delivery plan

1. **Episode traces:** transcript-delta capture, unique episode IDs, empty-turn
   exclusion, interrupted-episode recovery, Codex transcript normalization, and
   cross-adapter regression tests. Implemented in this branch.
2. **Project registry:** automatic project keys, named groups, conversation
   leases, and `adamast context` controls.
3. **Learning job protocol:** frozen snapshots, staging, claims, receipts,
   validation, activation, stale-worker recovery, and audit records. Implemented
   for Codex and Claude Code.
4. **External driver adapter:** move current provider learning behind the job
   protocol without changing batch behavior.
5. **Native workers:** Codex and Claude Code implemented.
6. **Adaptive refinement:** no-change reviews and project-local successor
   activation implemented for Codex; manual controls and rollback UI remain.
7. **UX parity:** Codex and Claude Code selectors and exactly-once notices are
   implemented; unified dashboard controls and migration UI remain.

### Codex selector prototype

The interactive prototype supports `session_selector: "prompt"` in both
`codex` and `claude_code` adapter configuration.
For Codex, `selector_surface: "browser"` makes the first real
`UserPromptSubmit` launch a session-bound localhost selector; `SessionStart`
only recovers an existing choice. This keeps background host tasks and spawned
agents from opening browser windows. The browser deterministically applies a
stored taxonomy, MAST, or AdaMAST-off under the program lock before reporting
success. `selector_surface: "inline"` preserves the prompt-based compatibility
path. A first substantive request becomes the episode task after selection,
while the selector exchange stays outside the episode task label.
Stored-taxonomy selection binds the existing
project/task-group program contract. AdaMAST-off bypasses gates and trace capture
for the conversation.

## Verification matrix

The implementation is not complete until tests cover:

- multiple turns in one Codex conversation;
- multiple turns in one Claude Code conversation;
- a trace containing only its episode delta;
- simultaneous Codex and Claude conversations in one project group;
- generation at the fifth eligible cross-harness episode;
- a native worker completing while the main episode continues;
- app exit and resume with a queued or running job;
- no mid-episode taxonomy switch;
- exact-version lock versus follow-latest behavior;
- adaptive refinement no-op and accepted successor paths;
- duplicate hooks, duplicate receipts, and stale worker recovery;
- redaction before storage and worker export;
- unchanged batch lifecycle and benchmark trace counts.
