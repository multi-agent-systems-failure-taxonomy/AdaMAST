# Claude Code integration

The Claude Code integration installs hooks that call the AdaMAST runtime at
session start, user-prompt submission, checkpoints, and final submission. It
supports both project-local operation and a user-level interactive mode shared
with Codex.

This page assumes the general AdaMAST package is already installed from the
[documentation home](index.md#install-adamast). Everything below is specific to
Claude Code.

## Install for every Claude Code conversation

```powershell
adamast-claude-install --user-level
adamast-doctor --claude-code
```

This merges AdaMAST into `~/.claude/settings.json` and writes
`~/.claude/adamast.json`; unrelated settings and plugins are preserved.
Claude Code resolves a Claude-owned program branch for every conversation.
Codex uses a separate branch even when its base `trace_output`, project root,
and logical task-group name match. This prevents traces, active learned
taxonomies, and native learning jobs from crossing between conversations or
hosts.

Restart an already-running Claude Code process after installing or updating the
hooks, then begin a new conversation so the new registration is loaded.

No external model API key, standalone `claude -p` process, or second login is
required for `claude_subagent`. A hook first asks the active Claude Code session
to launch a native generator Agent in the background and continue the user's
task immediately. After exact evidence checks, a separately claimed background
support-review Agent evaluates every replacement code. Each subtask
reads only its phase-specific frozen prompt and schema and returns a signed
receipt through `SubagentStop`. Foreground reconciliation activates between
episodes only after both phases pass.

If `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, AdaMAST leaves learning jobs queued
for a later background-capable session instead of running them in the
foreground.

One completed assistant episode is one trace. Generation starts after five
eligible traces by default, first refinement review after `k_init` (ten), and
later reviews every `k` traces (twenty). MAST or the current learned taxonomy
remains active while the worker runs. Trigger and completion notices appear in
Claude's visible `systemMessage` and agent-facing `additionalContext`.

After activation, Claude context names the active learned taxonomy by display
name and immutable ID. The original selector choice, including MAST, remains
recorded only as the lineage seed; checkpoints use the active taxonomy's codes.

Remove only the user-level AdaMAST registration with:

```powershell
adamast-claude-uninstall --user-level
```

## Install hooks

```bash
adamast-claude-install --project-dir . --config adamast.json
```

Then start Claude Code in that project.

AdaMAST will:

1. open the local taxonomy library for MAST, stored taxonomies, or `No taxonomy`;
2. hold the first substantive prompt until that choice is resolved;
3. record checkpoint reflections privately at configured boundaries;
4. block final completion until the final gate passes or exhausts the retry envelope;
5. record one canonical episode trace at each accepted Stop boundary;
6. trigger durable generation or refinement jobs when thresholds are reached.

## Live checkpoint monitor

Claude Code opens the same project/conversation monitor used by Codex. The
conversation selector shows Claude's current custom title instead of exposing
its session UUID, while the UUID remains the stable routing key in the URL.
Checkpoint cards are grouped either by gate or by failure mode, and selecting a
card opens its full evidence and decision details.

Built-in gates send the compact checkpoint directly to
`adamast-claude-checkpoint`; they do not print checkpoint blocks or long
reflection prompts into the conversation. Claude's per-prompt ID is attached to
every checkpoint created during that prompt, so several gates may remain
separate while still appearing under the same turn. A repair-required gate
continues to block until the recorded next action is addressed.

See [Live monitor](DASHBOARD.md) for navigation, timeline filters, and both
viewing modes.

If a native Agent subtask disappears without a receipt, the coordinator expires
its claim, keeps the current taxonomy active, and permits a retry from the same
frozen evidence. Automatic secret redaction runs before trace persistence by
default. Redaction is a defense in depth measure, not permission to place
credentials in task transcripts.

The user-level installer defaults to the browser selector. Use the inline
numbered fallback when needed:

```bash
adamast-claude-install --user-level --selector-surface inline
```

On the first substantive prompt, the synchronous `UserPromptSubmit` hook keeps
that prompt paused while the browser is open. Selecting a taxonomy completes
the hook and lets Claude process the original prompt immediately; do not send a
second message. If no choice is made before `worker_timeout_seconds`, AdaMAST
blocks that prompt without echoing its full text and the next prompt reopens the
selector.

Every new Claude conversation receives a durable isolated branch. Choosing a
stored taxonomy uses it only as that branch's immutable seed; choosing MAST
starts the branch from zero. Later traces and refinements never enter another
conversation's branch.

The taxonomy choice remains pinned to Claude's session ID. Resuming the
conversation from another shell or changing its current working directory does
not recompute the project, reopen the browser selector, or replace the selected
taxonomy.

For older inline-selector sessions, SessionStart also checks the transcript
after the saved selector boundary. An exact offered reply such as `MAST` is
migrated before the browser can reopen; ordinary task prose does not match.

## Customize built-in hooks

Examples:

```bash
# Disable the built-in subagent checkpoint.
adamast-claude-install --project-dir . --config adamast.json --disable-hook SubagentStop

# Only run post-tool advisory nudges after selected tools.
adamast-claude-install --project-dir . --config adamast.json --post-tool-use-matchers Bash,Edit,Write
```

You can also configure built-in hooks in `adamast.json`:

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

## Add custom hooks

Custom hooks are useful when you want AdaMAST to fire on a specific event or tool rather than every possible boundary.

```bash
adamast-claude-add-hook \
  --project-dir . \
  --name pre-bash \
  --event PreToolUse \
  --matcher Bash \
  --command-pattern "python .*eval" \
  --checkpoint-key fixed \
  --mode blocking
```

List hooks:

```bash
adamast-claude-list-hooks --project-dir .
```

Remove one hook:

```bash
adamast-claude-remove-hook --project-dir . --name pre-bash
```

Use `blocking` when the agent must satisfy the reflection contract before continuing. Use `advisory` when AdaMAST should nudge but not block.

`--command-pattern` narrows a broad tool matcher, for example `Bash`, to one
recurring command. `--checkpoint-key fixed` is useful for recurring gates that
should open one checkpoint and close it on the next matching event.

## Gates fail open

If an AdaMAST hook itself crashes or is killed at Claude Code's per-hook
timeout, the agent continues normally and that gate silently does not fire.
This is deliberate: an AdaMAST bug must never leave your session unable to
finish. The trade-off is that a skipped gate is quiet — when gating matters
(A/B runs, benchmarks), verify it happened rather than assuming:

- `[adamast]` lines on stderr report retry-guard releases and internal errors;
- `<trace_output>/decisions.log` records every gate decision and release;
- `adamast-status --config adamast.json` shows reflections recorded per session —
  a finished session with no final-gate evidence means the gate was skipped.

## Uninstall hooks

```bash
adamast-claude-uninstall --project-dir .
```

This removes AdaMAST hook config from the project. It does not delete learned taxonomies or trace folders.

## Source layout

The adapter source is organized under `adamast/hosts/claude_code/`.
