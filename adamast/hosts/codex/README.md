# adamast/hosts/codex/

Project-local Codex hook integration for AdaMAST.

Codex exposes lifecycle command hooks through `.codex/hooks.json`, so this
adapter registers AdaMAST at the Codex hook layer rather than relying only on a
passive skill. The optional skill package is still useful as reusable guidance,
but the main integration is hook-based.

Each Stop callback commits one episode trace from the current user turn, agent
work, tool activity, repairs, and directly recorded final checkpoint since the
previous Stop. The main agent sends the four gate fields to
`adamast codex checkpoint` rather than appending them to the assistant
message. Not all Codex Desktop builds redeliver Stop after a hook
continuation, so this adapter intentionally uses one callback rather than
depending on a second reflection-only turn.

The stored Codex trajectory is normalized JSONL. Human and assistant messages
plus tool calls/results are retained; developer/system context, reasoning,
hook prompts, installed-skill text, and token accounting are excluded. An
interrupted episode is closed on resume or the next substantive user prompt.

## Install hooks

```bash
adamast codex install \
  --project-dir /path/to/project \
  --trace-output /path/to/adamast-program \
  --adamast-model gpt-5
```

Or with a shared `adamast.json`:

```bash
adamast codex install --project-dir . --config adamast.json
```

Use `--selector-surface browser` (the default) or
`--selector-surface inline` to override `codex.selector_surface` for one
install.

For user-level hooks that apply to every Codex project, use automatic project
scope. In this mode `trace_output` is the AdaMAST interactive-data base rather
than one program directory:

```json
{
  "trace_output": "~/.adamast/interactive",
  "adamast_model": "gpt-5",
  "codex": {
    "project_scope": "auto",
    "task_group": "default",
    "session_selector": "prompt",
    "selector_surface": "browser",
    "learning_backend": "codex_subagent"
  }
}
```

Each event resolves to
`<trace_output>/projects/<project-key>/groups/<task-group>/program`. Git
subdirectories share the canonical Git root. Non-Git workspaces use their
resolved working directory. Set `codex.project_id` to intentionally share an
identity across worktrees or paths.

The selector shows the resolved project path. If tools explicitly run under a
different `cwd` or `workdir`, Stop still commits the trace but emits a visible
scope-mismatch warning; the runtime never silently moves an active conversation
between project programs.

With `session_selector` set to `prompt`, a new Codex conversation opens the
session-bound localhost taxonomy library from its first real
`UserPromptSubmit`. `SessionStart` only recovers an existing selection, so
background host work and spawned agent sessions cannot open unsolicited browser
windows. The prompt hook stays open while the catalog is visible. Once the
browser applies a choice, that same original prompt continues immediately and
becomes the episode task; the user does not need to submit it again. The
installer gives this hook the picker timeout plus a small shutdown margin while
keeping the other lifecycle-hook timeouts short.

The picker lists MAST (recommended) alongside compatible stored taxonomies
and `No taxonomy`. It runs in a detached, time-bounded process and writes a
durable activation receipt after updating Codex state. Choosing either MAST or a stored
taxonomy seeds a durable conversation-owned branch. Later traces, generation,
refinement, and activation remain inside that branch. `No taxonomy` disables
AdaMAST gates and trace capture for only that conversation.

`learning_backend: "codex_subagent"` uses a native subagent in the active
Codex task and does not require a standalone CLI login or external API key.
Every hook in an already selected conversation polls the durable project state:
it reconciles completed receipts, checks the generation or refinement threshold,
and idempotently queues any missing job. On the next `UserPromptSubmit` or
supported `SessionStart` boundary (`startup`, `resume`, or context compaction),
the active agent receives a claimed task and launches the taxonomy subagent
while normal work continues. Context compaction is included so a long-running
desktop task can dispatch a queued job even when that Codex build does not emit
`UserPromptSubmit`. Unselected internal sessions do not poll or claim learning
jobs. The subagent reads an immutable outcome-blind snapshot and returns a
staged receipt through `SubagentStop`; hook reconciliation alone owns
validation and activation. `SubagentStop` never claims the next phase: a
replacement's independent support review stays queued until the next supported
model-context boundary.

The installer writes:

```text
<project>/.codex/hooks.json
<project>/.codex/adamast.json
```

After install, open `/hooks` in Codex and trust the AdaMAST hooks. Codex records
trust against the hook definition hash, so changed hooks need review again.

## Default hook events

| Event | AdaMAST behavior |
|---|---|
| `SessionStart` | Recover AdaMAST state and dispatch queued native learning at startup, resume, or context compaction. |
| `UserPromptSubmit` | Open the taxonomy library for a new user conversation, wait for its choice, release the original prompt, and handle episode boundaries. |
| `Stop` | Validate the directly recorded final checkpoint, attach the exact turn ID, and commit the episode. |
| `SubagentStop` | Capture signed taxonomy-worker receipts; ordinary visible subagent text is never parsed as a checkpoint. |
| `PostToolUse` | Silent polling and durable-state reconciliation after supported successful tools. |

Routine polling intentionally emits no assistant text. Codex can show the
hook's transient `Polling AdaMAST` status while it runs. Tool failure review lives
in the always-loaded managed skill: when the agent receives an actual failed
tool result, it maps the evidence privately and records one `tool_failure`
checkpoint before the next tool call. The block is displayed by the web monitor,
not in the conversation.

Generation and refinement lifecycle notices are also user-visible once. They
remain in the durable manifest through terminal `Stop` and `SubagentStop`
events, then are consumed only by `SessionStart` or `UserPromptSubmit`, where
Codex documents model-context delivery. `PostToolUse` does not consume them.

Defaults can be customized:

```bash
adamast codex install \
  --project-dir . \
  --config adamast.json \
  --disable-hook SubagentStop \
  --post-tool-use-matchers Bash,Edit,Write
```

The config-file equivalent is top-level `codex_hooks`:

```json
{
  "codex_hooks": {
    "SubagentStop": false,
    "PostToolUse": {
      "enabled": true,
      "matchers": ["Bash", "Edit|Write"]
    }
  }
}
```

## Optional Codex skill

To also install the reusable Codex skill guidance:

```bash
adamast codex install --project-dir . --config adamast.json --install-skill
```

By default this writes `adamast-failure-modes` under the documented user skill
directory, `~/.agents/skills`. Pass `--skills-dir ./.agents/skills` for a
repository-local copy.

## Uninstall

```bash
adamast codex uninstall --project-dir .
```

This removes only AdaMAST hook registrations from `.codex/hooks.json` and deletes
`.codex/adamast.json`. To also remove the optional skill files:

```bash
adamast codex uninstall --project-dir . --remove-skill
```

For the zero-config user-level integration, use
`adamast codex install --user-level` and reverse it with
`adamast codex uninstall --user-level`.

## Programs

| File | Purpose |
|---|---|
| [`config.py`](config.py) | `CodexConfig` and hook-event configuration. |
| [`dispatcher.py`](dispatcher.py) | Command hook entry point; reads Codex hook JSON from stdin and emits JSON back to Codex. |
| [`runtime.py`](runtime.py) | SessionStart, Stop, SubagentStop, and PostToolUse behavior on top of `adamast`. |
| [`prompts.py`](prompts.py) | Prompt assets for the Codex hook integration. |
| [`checkpoint.py`](checkpoint.py) | Direct four-field checkpoint recorder used by the main Codex agent. |
| [`transcript.py`](transcript.py) | Codex-specific transcript normalization, filtering, bounds, and project-workdir detection. |
| [`install.py`](install.py) | Writes `.codex/hooks.json`, `.codex/adamast.json`, and optional skill files. |
| [`uninstall.py`](uninstall.py) | Removes AdaMAST hook registrations and optional skill files. |
| [`state.py`](state.py) | Per-session Codex hook state under the program trace output. |
| [`learning_jobs.py`](learning_jobs.py) | Codex policy facade for shared durable jobs and threshold polling. |
| [`subagent_protocol.py`](subagent_protocol.py) | Codex claim-instruction facade for the shared receipt protocol. |
| [`session_routes.py`](session_routes.py) | Codex-namespaced facade for conversation-owned branch routing. |
| [`browser_picker.py`](browser_picker.py) | Codex-namespaced facade for the shared localhost picker transport. |
| [`native_worker.py`](native_worker.py) | Legacy detached-worker compatibility entry point; the native runtime does not invoke it. |

Host-neutral implementations live in
[`../interactive/`](../interactive/). Keep Codex event parsing and transcript
normalization here; place selector, route, job, and receipt behavior in the
shared package.
