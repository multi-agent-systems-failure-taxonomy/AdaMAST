# AdaMAST 5-minute start

This page covers explicit project-local and pipeline integration. For the
shortest user-level Codex or Claude Code path, use
[Interactive setup](INTERACTIVE_SETUP.md).

If you want the full reference, start from the [documentation home](index.md).

## 1. Install

From GitHub:

```bash
python -m pip install adamast
```

From a local checkout:

```bash
cd /path/to/AdaMAST
python -m pip install .
```

Optional Anthropic SDK support:

```bash
python -m pip install "adamast[anthropic]"
```

Optional AWS Bedrock bearer-token support:

```bash
python -m pip install "adamast[bedrock]"
```

For Bedrock, set `AWS_BEARER_TOKEN_BEDROCK` and `AWS_REGION` /
`AWS_DEFAULT_REGION` in your shell. AdaMAST uses boto3's Bedrock Converse API
for this credential form.

AdaMAST never stores credential values. Set provider keys in your environment
instead.

## 2. Create one config file

Create `adamast.json` in your project:

```json
{
  "version": 1,
  "trace_output": "./adamast-program",
  "adamast_model": "gpt-5"
}
```

Use `adamast_model` for AdaMAST generation, judge, and refinement calls. If your
own program has a task-solving model, keep that separate.

Relative paths are resolved relative to the config file. Every other field has
a sensible default; the full reference is [CONFIGURATION.md](CONFIGURATION.md).

## 3. Check the install

```bash
adamast-doctor --config adamast.json
```

For Claude Code projects:

```bash
adamast-doctor --config adamast.json --claude-code
```

For Codex projects:

```bash
adamast-doctor --config adamast.json --codex
```

Warnings usually mean "AdaMAST can run, but a useful optional capability may be
missing." Errors mean the requested setup is not ready.

## 4A. Use AdaMAST with Claude Code

For every Claude Code project with native in-session learning, the shorter path
is `adamast-claude-install --user-level`. The command below is the explicit,
project-local provider-backed path.

Install project-local hooks:

```bash
adamast-claude-install --project-dir . --config adamast.json
```

Start Claude Code in that project. AdaMAST will:

1. start with inherited taxonomy if configured, otherwise built-in MAST;
2. deliver checkpoint instructions at configured hook boundaries;
3. require the final submission gate before completion;
4. record one canonical trace for each completed assistant episode;
5. trigger generation/refinement when configured thresholds are reached.

Useful hook customization examples:

```bash
# Do not fire the built-in subagent checkpoint.
adamast-claude-install --project-dir . --config adamast.json --disable-hook SubagentStop

# Only nudge after selected successful tool calls.
adamast-claude-install --project-dir . --config adamast.json --post-tool-use-matchers Bash,Edit,Write

# Add a custom blocking gate before Bash calls.
adamast-claude-add-hook --project-dir . --name pre-bash --event PreToolUse --matcher Bash --mode blocking
```

List installed custom hooks:

```bash
adamast-claude-list-hooks --project-dir .
```

Remove AdaMAST hooks without deleting learned traces or taxonomies:

```bash
adamast-claude-uninstall --project-dir .
```

## 4B. Use AdaMAST with Codex hooks

For every Codex project with native in-task learning, the shorter path is
`adamast-codex-install --user-level`. The command below is the explicit,
project-local provider-backed path.

Install project-local Codex hooks:

```bash
adamast-codex-install --project-dir . --config adamast.json
```

This writes `.codex/hooks.json` and `.codex/adamast.json`. Open `/hooks`
inside Codex and trust the AdaMAST hooks before relying on them.

Default Codex events:

1. `SessionStart`: recover standing AdaMAST context for a selected conversation.
2. `UserPromptSubmit`: open the taxonomy library for a new conversation and handle episode boundaries.
3. `Stop`: capture the compact final checkpoint and commit the episode once.
4. `SubagentStop`: capture a checkpoint when present without blocking.
5. `PostToolUse`: poll durable AdaMAST state after supported successful tools.

Routine polls remain silent apart from Codex's transient hook status. The
managed skill tells the agent to show one compact checkpoint after an actual
tool failure. Generation/refinement state changes appear once through the next
`SessionStart` or `UserPromptSubmit`; ordinary successful hooks do not add
assistant messages to the conversation.

Optional skill guidance:

```bash
adamast-codex-install --project-dir . --config adamast.json --install-skill
```

Remove it with:

```bash
adamast-codex-uninstall --project-dir .
```

## 4C. Use AdaMAST around one LLM call

This path is for scripts, notebooks, benchmarks, or any application where you
own the model call.

```bash
adamast-single-run \
  --config adamast.json \
  --task "Solve the task, then pass through AdaMAST before final answer." \
  --model gpt-5
```

The `--model` flag is the task-solving model. `adamast_model` in `adamast.json` is
still the AdaMAST judge/generation/refinement model.

## 5. Watch the dashboard

If `dashboard` is true, integrations can launch the dashboard automatically.
To open it manually:

```bash
adamast-dashboard \
  --trace-output ./adamast-program \
  --store-dir ~/.adamast/taxonomies
```

The dashboard is read-only and binds to localhost by default.

## 6. Verify data is being written

After a run, inspect trace state:

```bash
adamast-traces status --config adamast.json
```

List stored taxonomies:

```bash
adamast-find --list
```

If `--inherit` is omitted, the run starts with built-in MAST. MAST is not stored
as a picker record. Generated/refined taxonomies become stored records only
after acceptance.

## 7. Common first-run choices

The fields most people touch first:

| Choice | Default | When to change it |
|---|---:|---|
| `generation_threshold` | `5` | Raise it if early traces are noisy or not representative. |
| `freeze` | `false` | Turn on for inference-only evaluation: record traces/evidence, but skip generation and refinement. |
| `repair_rounds` | `3` | Final-gate repair opportunities before honest unresolved release (`max_retries` is the legacy alias). |

Every field, with defaults and semantics, is in
[CONFIGURATION.md](CONFIGURATION.md).

## 8. Where to customize

Most user-facing behavior is now in Markdown or JSON assets. Start with
[`CUSTOMIZATION.md`](CUSTOMIZATION.md) before editing Python.
