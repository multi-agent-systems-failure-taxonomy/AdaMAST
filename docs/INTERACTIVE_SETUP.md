# Interactive setup

This is the shortest path for using AdaMAST in ordinary Codex or Claude Code
conversations. It does not require `adamast.json` or a separate model API key.

## Choose a host

| Host | Install command | Native learning process |
|---|---|---|
| Codex | `adamast-codex-install --user-level` | Native subagent in the active task |
| Claude Code | `adamast-claude-install --user-level` | Native Agent subtask in the active session |
| Both | Run both commands | Shared project/task-group taxonomy state |

The hooks and trace runtime work in the host conversation. In Codex and Claude
Code, taxonomy generation and refinement use a native generator plus an
independent native support reviewer while the main agent keeps working normally.
Durable polling repairs a missed threshold trigger on the next lifecycle event.

## 1. Install the package

Until the first PyPI release is published, install directly from GitHub:

```bash
python -m pip install --upgrade adamast
```

## 2A. Enable Codex

```bash
adamast-codex-install --user-level
adamast-doctor --codex
```

The installer writes `~/.codex/hooks.json` and
`~/.codex/adamast.json`, then installs the guidance skill under
`~/.agents/skills/adamast-failure-modes`. Open `/hooks` in Codex and trust the
AdaMAST hooks.

Codex taxonomy learning uses the active task's native subagent capability. It
does not need a separately runnable Codex CLI, a second login, or an external
model API key. `adamast-doctor --codex` verifies the hook and configuration
contract before generation reaches its default five-trace threshold. The
installed `SessionStart` hook includes context compaction, allowing a queued
native job to reach the active task even when an already-running desktop task
does not emit `UserPromptSubmit`.

## 2B. Enable Claude Code

```bash
adamast-claude-install --user-level
adamast-doctor --claude-code
```

The installer merges AdaMAST hooks into `~/.claude/settings.json` and writes
`~/.claude/adamast.json`. It preserves unrelated settings and plugins.
The doctor verifies the installed hook and configuration contract. Native
learning uses the already-running Claude Code session; it does not invoke a
separate CLI login.

## 3. Start a conversation

A new Codex task opens the local taxonomy library after its first user message;
Claude Code opens it from `SessionStart`. Its choice list contains:

```text
MAST  [Recommended]
Compatible stored taxonomies
No taxonomy
```

The browser applies the choice directly before showing the activation page; it
does not wait for a later host message. `No taxonomy` disables AdaMAST only for
that conversation.
The browser catalog shows every locally stored taxonomy with a human-readable
name, scope, summary, code count, and detailed failure codes. This global list
does not bind or import anything into the current project until it is selected.
Choosing one binds it to the project/task group immediately.
If the project already has a learned taxonomy, the selector shows it as the
recommended first choice and shows MAST as a separate numbered choice. Selecting
MAST creates an isolated `fresh-*` task group for that conversation, allowing a
new taxonomy to be learned from zero without replacing the shared default.

One completed assistant episode becomes one trace. By default:

- trace 5 queues the first learned taxonomy;
- trace 10 after activation queues the first refinement review;
- later reviews run every 20 new traces.

The active taxonomy remains stable while a worker runs. Trigger,
support-review, and completion notices appear in the conversation; completion
appears on the next lifecycle event when the host cannot inject into an idle
conversation.

Native replacement candidates contain 1 to 30 failure codes. Each code must
cite the frozen trace IDs that support it, quote an exact span from every cited
trace, and include a rationale. AdaMAST verifies those spans before activation
and keeps the result for audit. A refinement `no_change` receipt contains no
replacement codes and leaves the stored taxonomy byte-for-byte unchanged.

## Shared project state

User-level installations resolve the Git root for each task and store state at:

```text
~/.adamast/interactive/projects/<project-key>/groups/default/program
```

Codex and Claude Code use the same path and runtime identity, so tasks started
from the same Git project share the active taxonomy and refinement history.
Different projects remain isolated. Set a stable `project_id` when the host
workspace differs from the repository being edited.

## Remove the integration

```bash
adamast-codex-uninstall --user-level
adamast-claude-uninstall --user-level
```

Uninstalling hooks does not delete learned taxonomies or trace history under
`~/.adamast`.

For explicit provider models, custom thresholds, or repository-committed hook
configuration, use the [project-local setup](GETTING_STARTED.md).
