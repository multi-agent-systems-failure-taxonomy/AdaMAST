# Codex integration

Register AdaMAST with Codex so the agent checks its work against known failure
modes while it runs and, after enough completed tasks, learns a catalog
specific to your project. The integration installs user-level or project-local
hooks that call AdaMAST from Codex session and boundary events.

!!! note
    This page assumes the general AdaMAST package is already installed from
    the [documentation home](index.md#install-adamast). Everything below is
    specific to Codex.

## 🚀 Install (zero configuration)

1. Register AdaMAST for every Codex conversation:

    ```bash
    adamast codex install --user-level
    ```

2. Check the install:

    ```bash
    adamast doctor --codex
    ```

3. Open `/hooks` inside Codex and trust the installed AdaMAST hooks.

4. If Codex Desktop was already running when you installed or updated
   AdaMAST, fully quit and reopen it before starting the first AdaMAST
   conversation. Opening a new conversation inside the old Desktop process is
   not sufficient to reload new hook registration.

No `adamast.json` or separate model API key is required. The installer writes
`~/.codex/hooks.json`, `~/.codex/adamast.json`, and the guidance skill at
`~/.agents/skills/adamast-failure-modes`.

The defaults are automatic Git-project scoping, task group `default`, the
conversation selector, generation after five traces, and native
`codex_subagent` learning in the active task. Native learning uses the task's
existing Codex session, so no separately runnable CLI or second login is
required. Taxonomy subagents run in the background; the main task does not
wait for them.

**Make it yours:**

| I want to… | Do this instead |
|---|---|
| Install for **one project** only | `adamast codex install --project-dir . --config adamast.json`; see the next section |
| Pick taxonomies **inline in chat** instead of a browser page | add `--selector-surface inline` |
| Undo the user-level install | `adamast codex uninstall --user-level` |

## 🧩 Project-local install

```bash
adamast codex install --project-dir . --config adamast.json
```

This writes:

- `.codex/hooks.json`
- `.codex/adamast.json`

Open `/hooks` inside Codex and trust the AdaMAST hooks before relying on them.
Restart an already-running Codex Desktop process after changing these files.

The user-level command enables the conversation selector automatically. For a
project-local install, configure it explicitly:

```json
{
  "trace_output": "~/.adamast/interactive",
  "adamast_model": "interactive-session",
  "codex": {
    "project_scope": "auto",
    "task_group": "default",
    "session_selector": "prompt",
    "selector_surface": "browser",
    "learning_backend": "codex_subagent"
  }
}
```

The installer flag is equivalent and overrides `adamast.json` for that
install:

```bash
adamast codex install --project-dir . --config adamast.json --selector-surface inline
```

!!! tip "Optional skill guidance"
    ```bash
    adamast codex install --project-dir . --config adamast.json --install-skill
    ```

    This copies the AdaMAST guidance skill into the documented user skill
    location, `~/.agents/skills`. Pass `--skills-dir ./.agents/skills` when
    you explicitly want a repository-local copy instead.

!!! note "Working from the wrong folder?"
    The selector includes the resolved project path. Start a task from the
    actual repository, or set `codex.project_id`, when the conversational
    workspace and the repository being edited differ. Explicit external tool
    workdirs produce a scope warning rather than silently rebinding taxonomy
    state.

## 🧭 The conversation selector

A new conversation opens the localhost AdaMAST catalog from its first real
`UserPromptSubmit`. Deferring the launch prevents Codex background tasks and
spawned agent sessions from opening selectors during their own startup. The
prompt hook remains paused while the user chooses; the same original prompt
then continues as the first episode task without requiring a second
submission. A selection timeout stops the original turn and lets the next
submission reopen the library safely.

The catalog recommends MAST, the built-in general-purpose catalog of 14
common agent failure modes ([what is MAST?](CONCEPTS.md#the-starting-taxonomy)),
and includes compatible stored taxonomies plus
`No taxonomy`. Its `/choose` handler validates the session's allowed options,
updates Codex state, and seeds the **conversation branch**: that
conversation's isolated program state.

| Choice | What it means |
|---|---|
| **MAST** | Starts that branch from the built-in taxonomy and learns from zero. |
| **A stored taxonomy** | An immutable lineage seed, not a shared mutable default. |
| **`No taxonomy`** | Disables AdaMAST checkpoints and trace capture only for that conversation. |

Each new conversation chooses independently. Either way, only the branch's own
traces can generate or refine its taxonomy head.

Catalog and chat surfaces use `display_name` when present and otherwise fall
back to the taxonomy domain. The generated `taxonomy_id` remains visible as
secondary metadata and continues to be the immutable storage and lineage key.

Set `selector_surface` to `"inline"` when opening a local browser is
undesirable. Both surfaces resolve the choice during `UserPromptSubmit`; the
browser remains the default because it provides the complete searchable
taxonomy library.

!!! note "Older inline-selector tasks"
    When upgrading an older inline-selector task, `SessionStart` also checks
    the transcript after the saved selector boundary. An exact offered reply
    such as `MAST` is migrated before the next prompt can open a browser;
    ordinary task prose does not match. New selector state is never created at
    `SessionStart`, so background host tasks and spawned agents cannot open a
    browser on startup.

## 🪝 Default events

The baseline install registers five Codex events; nothing to configure:

| Event | What AdaMAST does |
|---|---|
| `SessionStart` | Deliver standing instructions and recover context for an already selected conversation. |
| `UserPromptSubmit` | Open a new conversation's taxonomy library, wait for the choice, release the original prompt, and handle each later episode boundary. |
| `Stop` | The blocking final checkpoint: validate the directly recorded gate, attach the exact Codex turn ID, and commit the episode trace in one callback. |
| `SubagentStop` | Capture compact subagent checkpoints and signed learning-worker receipts without blocking. |
| `PostToolUse` | Poll durable AdaMAST state after supported successful tools; nudge when a tool result carries a failure signature. |

## 🤫 What appears in chat

Routine hook calls are sensors, not chat messages. Codex may show the
transient status message attached to each hook while it runs, but successful
`PostToolUse` polls, ordinary state reconciliation, repeated standing context,
and duplicate Stop callbacks do not add assistant messages. The always-loaded
AdaMAST skill tells the agent to record one compact checkpoint after an actual
tool failure and before its next tool call. Checkpoint fields are written to
the local monitor rather than rendered as assistant text.

Taxonomy generation/refinement triggers, activation, retention, and failure
produce one concise lifecycle update. The
[Codex Hooks documentation](https://learn.chatgpt.com/docs/hooks) documents
`additionalContext` for `SessionStart` and `UserPromptSubmit`, not
`PostToolUse`, so AdaMAST only consumes queued lifecycle notices at those two
model-context events.

!!! note "Why a notice can arrive one turn late"
    Learning notices are not consumed by `Stop` or `SubagentStop`, because
    those events occur after the model has produced the response and cannot
    reliably render new conversation text. The notice remains durable until
    the next `SessionStart` or `UserPromptSubmit` event can deliver it to the
    active model. The hook also emits a `systemMessage` for Codex surfaces
    that show hook messages directly.

## 🧾 The checkpoint contract

Codex still creates the same compact fields at every existing checkpoint:
`Checkpoint`, `Relevant codes`, `Evidence`, and `Next action`. It now sends
them to `adamast codex checkpoint` before continuing instead of appending them
to the assistant message. The conversation-specific recorder path and session
ID are supplied through hook context; users do not need to run this command
manually.

The final gate uses `--gate stop`. The Stop hook validates the already
recorded gate, attaches the exact Codex turn ID, and closes the episode in its
first callback. The previous visible-block parser remains a compatibility
fallback for conversations using an older managed skill. A missing or invalid
gate is reported, but the episode is still closed so project state cannot
remain stranded.

Successful checkpoint capture is silent in the conversation. The same content
appears immediately in the localhost monitor, where it can be expanded into
its Observe, Correlate/Evidence, Map, and Decide/Next action fields.

!!! note "What goes into learning traces"
    Learning traces use normalized Codex JSONL. Developer/system messages,
    reasoning payloads, hook prompts, globally installed skill content, and
    token events are excluded; human/assistant messages and bounded tool
    interactions are retained. Resume and the next user prompt recover
    unfinished episodes.

## 🧠 Native taxonomy learning

`codex.learning_backend: "codex_subagent"` runs generation and refinement in
a native subagent of the active Codex task. It does not require a standalone
`codex` executable, separate CLI login, `OPENAI_API_KEY`, or another
user-supplied model credential. The subagent receives a claimed job and may
read only its immutable project/task-group evidence snapshot and output
schema.

The fifth eligible episode triggers generation by default; the first
refinement review occurs after 10 new episodes and later reviews every 20;
thresholds and counters are detailed in
[Traces and learning](TRACES_AND_LEARNING.md), the worker protocol in
[Native taxonomy learning](NATIVE_LEARNING.md). Every lifecycle hook also
polls the durable program: if enough eligible episodes exist but no job was
queued, the next hook repairs the missed trigger idempotently. MAST remains
active while the subagent produces a proposal, and a review may retain the
current taxonomy when the evidence does not justify a change.

The subagent cannot publish a taxonomy: it returns a bounded receipt in its
final message, which `SubagentStop` passes to the normal hook coordinator for
validation and activation only when no episode is active.

After activation, Codex context names the active learned taxonomy by display
name and immutable ID. The original selector choice, including MAST, remains
recorded only as the lineage seed; it does not remain the checkpoint
vocabulary.

!!! note "Evidence rules for proposed codes"
    The native worker candidate schema accepts 1 through 30 replacement
    codes. Thirty is a safety cap, not a target, so a small five-trace
    generation snapshot may produce fewer codes. Every proposed code must
    cite one or more frozen trace IDs, include an exact quote from every
    cited trace, and explain the support. AdaMAST checks the quotes against
    the immutable snapshot and stores the validation record inline for audit.
    A refinement that chooses `no_change` returns no codes; the coordinator
    retains the current taxonomy verbatim.

Codex hooks cannot inject into an idle task asynchronously. Trigger and
finish notices therefore remain queued through terminal `Stop` and
`SubagentStop` events, then appear exactly once on the next `SessionStart` or
`UserPromptSubmit` event. A failed or stale result leaves MAST or the current
taxonomy active and preserves traces.

`codex.worker_timeout_seconds` controls the native claim lease and the
maximum browser-selection wait. The installer gives the browser
`UserPromptSubmit` hook that budget plus a short shutdown margin. The legacy
`codex.worker_model` and `codex.codex_cli_path` fields remain readable for
configuration compatibility but are not used by the in-task worker.

## 🎛️ Custom hook policy

!!! note "How hooks get enabled in Codex"
    The installer registers the hooks automatically, but Codex requires one
    manual trust step before they run: open `/hooks` inside Codex and trust
    the AdaMAST hooks. Codex records trust against the hook definition hash,
    so after installing an AdaMAST update that changes a hook you must trust
    it again.

The five baseline events are listed under [Default events](#default-events).
Every event can be switched off, and `PostToolUse` can be narrowed to specific
tools. To add more events or decide which fit your workflow, see the
[Codex Hooks documentation](https://learn.chatgpt.com/docs/hooks).

Use `codex.hooks` in `adamast.json` when you want AdaMAST to trigger only on
selected Codex events:

```json
{
  "codex": {
    "hooks": {
      "SessionStart": true,
      "UserPromptSubmit": true,
      "Stop": true,
      "SubagentStop": true,
      "PostToolUse": {
        "enabled": true,
        "matchers": ["shell_command", "apply_patch"]
      }
    }
  }
}
```

!!! tip
    Keep the direct final checkpoint on `Stop`; use advisory hooks for
    noisier events.

## 🧹 Uninstall

```bash
# User-level
adamast codex uninstall --user-level

# Project-local
adamast codex uninstall --project-dir .
```

This removes AdaMAST hook config and, for the user-level default, the managed
guidance skill. It does not delete learned taxonomies or trace folders.

## 🗂️ Source layout

The adapter source is organized under `adamast/hosts/codex/`.

Continue with [Live monitor](DASHBOARD.md) or
[Troubleshooting](TROUBLESHOOTING.md).
