# AdaMAST Claude Code integration

Claude Code runtime skin. It delivers the active taxonomy only at reflection
gates, records live firing evidence, and captures one canonical learning trace
for each completed main-agent turn. Taxonomy generation, refinement, and
storage remain engine-owned and are invoked through the public lifecycle API.

## Natural-language assets

Claude Code can run AdaMAST through the installed Python hooks, or a harness can
read the natural-language assets directly and supply its own execution layer:

- `adamast/hosts/claude_code/assets/standing_prompt.md`: standing session
  instruction delivered at `SessionStart`.
- `adamast/protocol/assets/checkpoint_reflection.md`: shared checkpoint
  reflection contract used by blocking/advisory gates.
- `adamast/hosts/claude_code/assets/final_gate_tail.md`: Claude-specific
  final submission tail appended to the shared checkpoint prompt.
- `adamast/judges/assets/<judge>/system.md` and `user.md`: simple judge prompts
  for Selection, Mapping, Coverage, Quality, Calibration.
- `adamast/judges/reflection_judge/assets/*.md`: Reflection Judge staged prompts.
- `adamast/learning/vendor/pipeline/assets/*.md`: taxonomy-generation prompts.

The Python modules remain useful as controllers: they load context, render
assets, validate JSON, record traces, and call the lifecycle API. Direct
natural-language import is for harnesses that want to own those mechanics
themselves.

Install the package, then install project-local hooks:

```powershell
adamast claude install `
  --project-dir C:\path\to\project `
  --trace-output C:\path\to\program-traces `
  --trace-root C:\path\to\learning-traces `
  --adamast-model claude-sonnet-4-6
```

Registrations invoke the installed module rather than a source-checkout file.
Uninstall with:

```powershell
adamast claude uninstall --project-dir C:\path\to\project
```

When upgrading from the old global `adamast-failure-modes` hooks, add
`--migrate-legacy-global` to either command. Unrelated Claude settings are
preserved.

For the Codex-style interactive experience in every Claude Code project, use:

```powershell
adamast claude install --user-level
adamast doctor --claude-code
```

User-level installation writes `~/.claude/adamast.json` and merges hooks
into `~/.claude/settings.json`. Automatic routing creates one Claude-owned
program branch per conversation. Neither another Claude conversation nor Codex
can claim its traces, active taxonomy head, or learning job.
The installer also writes `.claude/agents/adamast-taxonomy-worker.md`. The
native Claude worker is one background Agent subtask in the active session;
it needs no separate model API key, standalone `claude -p` process, or second
login. Swap the default browser selector for the inline numbered surface with
`--selector-surface inline`.

For the browser surface, Claude Code's first `UserPromptSubmit` hook remains
open until the user chooses. A successful choice releases that same original
prompt with the selected taxonomy context, so no follow-up prompt is required.
The installed hook timeout includes the configured selector/worker timeout plus
a short shutdown margin.

AdaMAST pins the first resolved project and task group to Claude's stable session
ID. Resuming that session from another shell, changing directories, or entering
a nested repository does not reopen the selector or change its taxonomy. The
shared live monitor displays Claude's latest custom conversation title while
retaining that session ID as the routing key.

Reverse the user-level install with `adamast claude uninstall --user-level`;
unrelated Claude settings remain.

The installer verifies the locally installed Claude Code binary before writing
`.claude/settings.local.json`. Built-in events:

- `SessionStart`: select and hold the session taxonomy; inject only standing
  checkpoint instructions.
- `UserPromptSubmit`: resolve the selector, hold an initial substantive task,
  and begin each later episode.
- `SessionEnd`: idempotent fallback capture for interrupted sessions that did
  not finish through the Stop gate.
- `TaskCompleted`: blocking sub-task checkpoint.
- `SubagentStop`: blocking subagent checkpoint, except for a signed taxonomy
  receipt, which is captured without recursively gating the learning worker.
- `Stop`: blocking full submission gate.
- `PostToolUse`: nonblocking nudge when a nominally successful tool response
  contains a failure signature.
- `PostToolUseFailure`: nonblocking nudge for actual tool execution failures.

All built-ins are installed by default for backwards compatibility, but you can
reduce noise for a project. Disable an event:

```powershell
adamast claude install `
  --project-dir C:\path\to\project `
  --trace-output C:\path\to\program-traces `
  --adamast-model claude-sonnet-4-6 `
  --disable-hook SubagentStop
```

Restrict successful/failed tool-result nudges to specific Claude Code tool
matchers:

```powershell
adamast claude install `
  --project-dir C:\path\to\project `
  --trace-output C:\path\to\program-traces `
  --adamast-model claude-sonnet-4-6 `
  --post-tool-use-matchers Bash,Edit,Write `
  --post-tool-use-failure-matchers Bash
```

The config-file equivalent is the top-level `built_in_hooks` object:

```json
{
  "built_in_hooks": {
    "SubagentStop": false,
    "PostToolUse": ["Bash", "Edit", "Write"],
    "PostToolUseFailure": {
      "enabled": true,
      "matchers": ["Bash"]
    }
  }
}
```

Only `PostToolUse` and `PostToolUseFailure` support matcher lists. Other
built-in events are on/off.

Taxonomy content is supplied privately only when a checkpoint fires. Built-in gates ask
Claude to send four compact fields through the private
`adamast claude checkpoint` recorder; checkpoint text is not printed in the
conversation. Accepted reflections write taxonomy-version-scoped runtime evidence to
`<trace-output>/.adamast-runtime-evidence.json`; the live dashboard overlays it
without changing the taxonomy record. Every checkpoint created in one Claude
prompt retains its own checkpoint ID and gate, and receives the same `prompt_id`
when the hook accepts it so the monitor can show the relationship without
merging entries.

Observed on Claude Code 2.1.181: `TaskCompleted` applies to explicit Claude
task objects. A main task that creates no task object is still covered by
`Stop`, but that version does not emit `TaskCompleted` for the main task.

On a successful privately recorded Stop release, the
adapter records the Claude JSONL delta since the previous accepted Stop as one
`GenerationTrace` and calls `adamast.record_trace()` followed by
`end_session()`. The next user turn opens a new runtime episode under the same
conversation lineage. The `SessionEnd` hook captures only an open episode that
Stop did not already commit. This lets the existing engine trigger generation
or refinement at its configured thresholds without duplicating learning logic
in the harness.

With `claude_code.learning_backend: "claude_subagent"`, `end_session()` freezes
eligible episode traces and queues one proposal-only job. The next
`SessionStart` or `UserPromptSubmit` claims it and instructs the active Claude
session to launch one native Agent subtask in the background against
`prompt.txt` and the strict output schema, then continue the user's task without
waiting. Its signed receipt is captured through `SubagentStop`.
Foreground reconciliation checks the claim, snapshot hash, evidence ids,
project version, and idle episode boundary before activation. Trigger and
finish notices are delivered exactly once to the originating conversation on
its next hook event.

When the final Stop reflection returns `REPAIR_REQUIRED`, the hook blocks and
grants one repair opportunity. The next completion attempt is blocked again
for a fresh reflection scoped to the repair trajectory. `Repair attempts used`
is checked against the hook-owned completed-repair counter. With the default
limit of three, Claude receives three repair-and-re-evaluate opportunities;
only a clean `READY_TO_SUBMIT` releases early, while a third still-unresolved
re-evaluation releases as an honest unresolved report to prevent an infinite
loop.

Claude discovery checks `CLAUDE_CODE_EXECUTABLE`, `claude` on `PATH`, and
common Windows, macOS, and Linux locations. The discovered installation must
contain every required event and blocking/additional-context contract.

For subprocess-based learning backends, `--openai-base-url` may be persisted.
Use `--openai-api-key-env NAME` to persist only the name of an inherited
environment variable; credential values are never written to disk.

Lifecycle controls exposed by the installer include generation threshold and
blocking, initial/standard refinement thresholds, refinement blocking,
advanced refinement, failure-nudge throttling, and `--skip-judge` (which
bypasses the end-of-generation Reflection Judge + refiner step). Run
`adamast claude install --help` for the exact options. `--no-dashboard`
persistently suppresses integration-managed dashboards when an outer harness
owns the dashboard.

## Custom hooks

Beyond the eight built-in events, you can bind the same reflection<->refinement
loop to **any** Claude Code event without writing Python. Use the
`adamast claude add-hook` CLI after `adamast claude install` has placed a
config:

```powershell
# Block before any Bash call; require an AdaMAST reflection before it runs.
adamast claude add-hook `
  --project-dir C:\path\to\project `
  --name pre-bash-gate `
  --event PreToolUse `
  --matcher Bash `
  --mode blocking

# Emit a non-blocking nudge whenever the user submits a new prompt.
adamast claude add-hook `
  --project-dir C:\path\to\project `
  --name on-user-prompt `
  --event UserPromptSubmit `
  --mode advisory
```

The CLI rewrites `.claude/adamast.json` and refreshes
`.claude/settings.local.json` so Claude Code picks up the new registration on
its next session. Inspect or remove:

```powershell
adamast claude list-hooks --project-dir C:\path\to\project
adamast claude remove-hook --project-dir C:\path\to\project --name pre-bash-gate
```

What each `--mode` does:

- **`blocking`**: On the first fire, exit 2 with a reflection prompt scoped
  to the activity around this event. On subsequent fires, parse the
  transcript for a valid `AdaMAST reflection:` block matching the checkpoint
  ID; release with exit 0 when valid. Retries are bounded by `--max-retries`
  (same hook-owned retry guard the built-in gates use); after the limit, the
  hook releases and logs the reason. The submission-only
  `READY_TO_SUBMIT`/`REPAIR_REQUIRED` language is intentionally omitted.
- **`advisory`**: Each fire emits an `additionalContext` nudge with a
  reflection prompt; Claude is never blocked. Any reflection block the
  assistant writes is harvested by the next blocking gate.

You can also declare custom hooks directly in `.claude/adamast.json`
under the top-level `"custom_hooks"` array; each entry is
`{name, event, mode, matcher?}`. The installer treats two custom hooks on the
same event with different matchers as separate `settings.local.json` entries
(so e.g. one Bash gate + one Edit nudge co-exist cleanly), and
`adamast claude uninstall` removes every custom registration alongside the
built-ins.

Available events: `SessionStart`, `SessionEnd`, `Stop`, `TaskCompleted`,
`SubagentStop`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
`PreCompact`, `Notification`, `UserPromptSubmit`. Built-in events keep
their built-in handler regardless; a custom hook on a built-in event
*adds* a second registration that runs alongside it.

## Programs

| File | Purpose |
|---|---|
| [`__init__.py`](__init__.py) | Public exports |
| [`config.py`](config.py) | `ClaudeCodeConfig` dataclass + built-in/custom hook specs, serialized to `.claude/adamast.json` and loaded by every hook |
| [`custom.py`](custom.py) | Reflection runtime for `CustomHookSpec` entries: `custom_blocking_checkpoint` + `custom_advisory` reuse the same reflection-shape validator as the built-in gates |
| [`dispatcher.py`](dispatcher.py) | Single command entry point. Built-in events route by `hook_event_name`; custom hooks route via `--custom <spec_name>` |
| [`install.py`](install.py) | `adamast claude install` CLI: write project-local or user-level settings + `adamast.json`, register built-in events + every `custom_hooks` entry, verify Claude Code binary contract |
| [`learning_jobs.py`](learning_jobs.py) | Claude policy facade for shared durable jobs, threshold polling, and foreground reconciliation |
| [`subagent_protocol.py`](subagent_protocol.py) | Claude Agent-instruction facade for the shared signed receipt protocol |
| [`session_routes.py`](session_routes.py) | Claude-namespaced facade for stable conversation-owned branch routing |
| [`browser_picker.py`](browser_picker.py) | Claude-namespaced facade for the shared localhost picker transport |
| [`native_worker.py`](native_worker.py) | Legacy detached-worker compatibility entry point; native in-session learning does not invoke it |
| [`manage_hooks.py`](manage_hooks.py) | `adamast claude add-hook` / `remove-hook` / `list-hooks` CLIs to mutate `custom_hooks` and refresh `settings.local.json` in one command |
| [`checkpoint.py`](checkpoint.py) | Private compact-checkpoint recorder used by Claude during built-in gates |
| [`prompts.py`](prompts.py) | Claude Code standing instruction, recorder context, and legacy gate-prompt compatibility |
| [`reflection.py`](reflection.py) | Compatibility re-export for the shared `adamast.core.reflection` parser |
| [`runtime.py`](runtime.py) | Hook behavior: session/episode lifecycle, private built-in checkpoints, tool polling, and transcript capture |
| [`state.py`](state.py) | Claude Code per-session hook state (mode, pending checkpoints); runtime evidence is recorded by `adamast.core.evidence` |
| [`transcript.py`](transcript.py) | Claude Code JSONL transcript readers/writers |
| [`uninstall.py`](uninstall.py) | `adamast claude uninstall` CLI: remove the hook registrations, preserve unrelated settings |

## Sub-folders

- [`hooks/`](hooks/): One file per Claude Code hook event
  (`SessionStart`, `UserPromptSubmit`, `SessionEnd`, `TaskCompleted`, `SubagentStop`, `Stop`,
  `PostToolUse`, `PostToolUseFailure`). Each file exports a thin `handle`
  function that the dispatcher routes to; all real behavior lives in
  [`runtime.py`](runtime.py).

Host-neutral implementations live in
[`../interactive/`](../interactive/). Keep Claude hook contracts and transcript
handling here; place selector, route, job, and receipt behavior in the shared
package.
