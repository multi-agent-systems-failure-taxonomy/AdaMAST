# Claude Code integration

Register AdaMAST with Claude Code so the agent checks its work against known
failure modes while it runs and, after enough completed tasks, learns a
catalog specific to your project. The integration installs hooks that call the
AdaMAST runtime at session start, user-prompt submission, checkpoints, and
final submission, either project-local or user-level (an interactive mode
shared with Codex).

!!! note
    This page assumes the general AdaMAST package is already installed from
    the [documentation home](index.md#install-adamast). Everything below is
    specific to Claude Code.

## 🚀 Install (zero configuration)

1. Register AdaMAST for every Claude Code conversation:

    ```bash
    adamast claude install --user-level
    ```

2. Check the install:

    ```bash
    adamast doctor --claude-code
    ```

3. Fully restart an already-running Claude Code process, then begin a **new
   conversation** so the new registration is loaded.

The installer merges AdaMAST into `~/.claude/settings.json` and writes
`~/.claude/adamast.json`; unrelated settings and plugins are preserved.

From then on, in each conversation AdaMAST will:

1. open the local taxonomy library for MAST (the built-in general-purpose
   catalog of 14 common agent failure modes; [what is MAST?](CONCEPTS.md#the-starting-taxonomy)),
   stored taxonomies, or `No taxonomy`;
2. hold the first substantive prompt until that choice is resolved;
3. record checkpoint reflections privately at configured boundaries;
4. block final completion until the final gate passes or exhausts the retry envelope;
5. record one canonical episode trace at each accepted Stop boundary;
6. trigger durable generation or refinement jobs when thresholds are reached.

**Make it yours:**

| I want to… | Do this instead |
|---|---|
| Install for **one project** only | `adamast claude install --project-dir . --config adamast.json`, then start Claude Code in that project |
| Pick taxonomies **inline in chat** (numbered) instead of a browser page | `adamast claude install --user-level --selector-surface inline` |
| Turn built-in hooks off, or narrow them to certain tools | see "Customize the hooks" below |
| Undo the user-level registration | `adamast claude uninstall --user-level` |

## 🧭 The taxonomy picker

The user-level installer defaults to the browser selector. On the first
substantive prompt, the synchronous `UserPromptSubmit` hook keeps that prompt
paused while the browser is open. Selecting a taxonomy completes the hook and
lets Claude process the original prompt immediately; do not send a second
message.

!!! note "If you never pick"
    If no choice is made before `worker_timeout_seconds`, AdaMAST blocks that
    prompt without echoing its full text, and the next prompt reopens the
    selector.

Your choice stays isolated and persistent:

- **Per conversation.** Every new Claude conversation receives a durable
  conversation branch. Choosing a stored taxonomy uses it only as that
  branch's immutable seed; choosing MAST starts the branch from zero. Later
  traces and refinements never enter another conversation's branch.
- **Per host.** Claude Code resolves a Claude-owned conversation branch for
  every conversation. Codex uses a separate branch even when its base
  `trace_output`, project root, and logical task-group name match. This
  prevents traces, active learned taxonomies, and native learning jobs from
  crossing between conversations or hosts.
- **Pinned to the session.** The taxonomy choice remains pinned to Claude's
  session ID. Resuming the conversation from another shell or changing its
  current working directory does not recompute the project, reopen the
  browser selector, or replace the selected taxonomy.

!!! note "Older inline-selector sessions"
    `SessionStart` also checks the transcript after the saved selector
    boundary. An exact offered reply such as `MAST` is migrated before the
    browser can reopen; ordinary task prose does not match.

## 🧠 Learning in the background

No external model API key, standalone `claude -p` process, or second login is
required for `claude_subagent` learning:

1. A hook asks the active Claude Code session to launch a native generator
   Agent in the background and continue the user's task immediately.
2. After exact evidence checks, a separately claimed background
   support-review Agent evaluates every replacement code.
3. Each subtask reads only its phase-specific frozen prompt and schema and
   returns a signed receipt through `SubagentStop`.
4. Foreground reconciliation activates between episodes only after both
   phases pass.

One completed assistant episode is one trace. By default the first generation
runs after five eligible traces, the first refinement review after `k_init`
(ten), and later reviews every `k` (twenty); thresholds and counters are
detailed in [Traces and learning](TRACES_AND_LEARNING.md), the worker
protocol in [Native taxonomy learning](NATIVE_LEARNING.md).

MAST or the current learned taxonomy remains active while the worker runs.
Trigger and completion notices appear in Claude's visible `systemMessage` and
agent-facing `additionalContext`.

After activation, Claude context names the active learned taxonomy by display
name and immutable ID. The original selector choice, including MAST, remains
recorded only as the lineage seed; checkpoints use the active taxonomy's
codes.

If a native Agent subtask disappears without a receipt, the coordinator
expires its claim, keeps the current taxonomy active, and permits a retry from
the same frozen evidence.

!!! note "Background tasks disabled?"
    If `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, AdaMAST leaves learning jobs
    queued for a later background-capable session instead of running them in
    the foreground.

## 📊 Live checkpoint monitor

Claude Code opens the same project/conversation monitor used by Codex. The
conversation selector shows Claude's current custom title instead of exposing
its session UUID, while the UUID remains the stable routing key in the URL.

Built-in checkpoints send their compact fields directly to
`adamast claude checkpoint`; they do not print checkpoint blocks or long
reflection prompts into the conversation. Claude's per-prompt ID is attached
to every checkpoint created during that prompt, so several checkpoints may
remain separate while still appearing under the same turn. A repair-required
checkpoint continues to block until the recorded next action is addressed.

Card grouping, navigation, timeline filters, and both viewing modes are
covered in [Live monitor](DASHBOARD.md).

!!! note "Secrets in traces"
    Automatic secret redaction runs before trace persistence by default.
    Redaction is a defense in depth measure, not permission to place
    credentials in task transcripts.

## 🎛️ Customize the hooks

!!! note "Hooks are enabled automatically"
    The installer registers all eight built-in hooks for you; there is
    nothing to enable by hand. A **user-level** install needs no approval:
    hooks run in your next new conversation. For a **project-level**
    install, Claude Code itself asks once per project before running hooks
    from that project's settings; accepting that prompt completes the setup.

**Built-in hooks** can be disabled or narrowed at install time:

```bash
# Disable the built-in subagent checkpoint.
adamast claude install --project-dir . --config adamast.json --disable-hook SubagentStop

# Only run post-tool advisory nudges after selected tools.
adamast claude install --project-dir . --config adamast.json --post-tool-use-matchers Bash,Edit,Write
```

…or in `adamast.json`:

```json
{
  "claude_code": {
    "built_in_hooks": {
      "SubagentStop": false,
      "PostToolUse": {
        "enabled": true,
        "matchers": ["Bash", "Edit", "Write"]
      },
      "PostToolUseFailure": ["Bash"]
    }
  }
}
```

**Custom hooks** make AdaMAST fire on a specific event or tool rather than
every possible boundary:

```bash
adamast claude add-hook \
  --project-dir . \
  --name pre-bash \
  --event PreToolUse \
  --matcher Bash \
  --command-pattern "python .*eval" \
  --checkpoint-key fixed \
  --mode blocking
```

List or remove them:

```bash
adamast claude list-hooks --project-dir .
adamast claude remove-hook --project-dir . --name pre-bash
```

| Option | When to use it |
|---|---|
| `--mode blocking` | The agent must satisfy the reflection contract before continuing. |
| `--mode advisory` | AdaMAST should nudge but not block. |
| `--command-pattern` | Narrows a broad tool matcher, for example `Bash`, to one recurring command. |
| `--checkpoint-key fixed` | Recurring events that should open one checkpoint and close it on the next matching event. |

## 🛟 Gates fail open

!!! warning "A skipped gate is quiet"
    If an AdaMAST hook itself crashes or is killed at Claude Code's per-hook
    timeout, the agent continues normally and that checkpoint silently does
    not fire. This is deliberate: an AdaMAST bug must never leave your
    session unable to finish.

When gating matters (A/B runs, benchmarks), verify it happened rather than
assuming:

- `[adamast]` lines on stderr report retry-guard releases and internal errors;
- `<trace_output>/decisions.log` records every gate decision and release;
- `adamast status --config adamast.json` shows reflections recorded per
  session; a finished session with no final-gate evidence means the gate was
  skipped.

## 🧹 Uninstall

```bash
# Remove only the user-level AdaMAST registration.
adamast claude uninstall --user-level

# Remove AdaMAST hook config from a project.
adamast claude uninstall --project-dir .
```

Neither command deletes learned taxonomies or trace folders.

## 🗂️ Source layout

The adapter source is organized under `adamast/hosts/claude_code/`.

Continue with [Live monitor](DASHBOARD.md) or
[Troubleshooting](TROUBLESHOOTING.md).
