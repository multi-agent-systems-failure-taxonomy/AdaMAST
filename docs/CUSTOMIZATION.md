# AdaMAST customization guide

Change what AdaMAST says, decides, or enforces — without touching runtime
controllers whenever possible. This page maps each kind of change to the one
file to edit, then shows how to verify your change.

Use this rule of thumb:

| You want… | Edit |
|---|---|
| different model-facing wording | Markdown |
| different declarative policy or defaults | JSON |
| different control flow, validation, storage, or integration behavior | Python |

## 🗺️ Safe customization map

| Goal | Edit |
|---|---|
| Change checkpoint reflection wording | `adamast/protocol/assets/checkpoint_reflection.md` |
| Change final-gate protocol | `adamast/protocol/assets/pre_submission_protocol.md` and, for Claude Code, `adamast/hosts/claude_code/assets/final_gate_tail.md` |
| Change Claude Code standing instruction | `adamast/hosts/claude_code/assets/standing_prompt.md` |
| Change single-LLM standing instruction | `adamast/hosts/single_llm/assets/standing_prompt.md` |
| Change simple judge prompts | `adamast/judges/assets/<judge>/` |
| Change Reflection Judge staged prompts | `adamast/judges/reflection_judge/assets/` |
| Change refinement prompt wording | `adamast/llm/assets/standard_refinement_prompt.md` and, in `adamast/learning/assets/`: `reflection_refiner_system.md`, `reflection_refiner_user.md`, `refinement_support_judge.md`, `refinement_repair.md` |
| Change taxonomy-generation prompt wording | `adamast/learning/vendor/pipeline/assets/` |
| Change classifier prompt wording | `adamast/learning/vendor/assets/classifier_system.md` and `classifier_user.md` |
| Change recognized model context profiles | `adamast/llm/assets/model_profiles.json` |
| Change Claude Code event lists | `adamast/hosts/claude_code/assets/hook_events.json` |
| Change Reflection Judge schema enums | `adamast/judges/reflection_judge/assets/schema_enums.json` |
| Change generation seed role definitions | `adamast/learning/vendor/pipeline/assets/role_definitions.json` |
| Change checker keyword lists | `adamast/learning/vendor/pipeline/assets/checker_terms.json` |
| Change config-file validation | `adamast/core/assets/adamast_config.schema.json` plus Python config loaders |

## 🪶 Config file first

For most deployments, prefer an `adamast.json` checked into the project over
long command lines. Every field and default is defined in the
[configuration reference](CONFIGURATION.md); a typical customization set looks
like this:

```json
{
  "version": 1,
  "trace_output": "./adamast-program",
  "adamast_model": "gpt-5",
  "generation_threshold": 5,
  "generation_stops": false,
  "k_init": 10,
  "k": 20,
  "freeze": false,
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

!!! note
    Explicit CLI flags override config values. Unknown config fields are
    rejected so misspellings do not silently change behavior.

## 🪝 Hook customization

Claude Code has two hook layers (the host workflow itself is covered in the
[Claude Code guide](CLAUDE_CODE.md)):

1. built-in hooks, configured with `claude_code.built_in_hooks`;
2. custom hooks, configured with `adamast claude add-hook` or the
   `claude_code.custom_hooks` array in `adamast.json`.

Built-in hook policy belongs in project config:

```json
{
  "claude_code": {
    "built_in_hooks": {
      "SubagentStop": false,
      "PostToolUse": ["Bash", "Edit", "Write"],
      "PostToolUseFailure": {
        "enabled": true,
        "matchers": ["Bash"]
      }
    }
  }
}
```

Custom hooks are for additional events:

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

Each custom hook creates a checkpoint at its event. Use `blocking` when the
agent must satisfy the reflection contract before continuing; use `advisory`
for a nudge that does not block.

`matcher` is the host tool/event matcher, usually tool-name granularity such
as `Bash`. `command_pattern` is an optional regex against `tool_input` /
command text, so one Bash hook can fire only for a recurring command. Use
`checkpoint_key` to control how repeated firings close:

| Value | Use |
|---|---|
| `tool_use_id` | Default; each host tool call is independent. |
| `command` | Same command text shares a checkpoint key. |
| `fixed` | The hook name has one stable in-flight checkpoint. |

## 🏷️ Taxonomy customization

A taxonomy record is selected only by `taxonomy_id`.

```json
{
  "taxonomy_id": "my-taxonomy-v1",
  "display_name": "Checkout Workflow Reliability",
  "repo": "display-only",
  "domain": "display-only",
  "codes": [
    {
      "id": "C-1",
      "name": "Observable failure name",
      "description": "Task-neutral diagnostic definition.",
      "category": "Custom"
    }
  ]
}
```

Register an existing taxonomy:

```bash
adamast register-taxonomy --file taxonomy.json --id my-taxonomy-v1
```

Use it:

```bash
adamast single-run --config adamast.json --inherit my-taxonomy-v1 --model gpt-5 --task "..."
```

!!! tip "Rename safely"
    `display_name`, `repo`, `domain`, and `summary` are display metadata.
    They do not route, group, or select taxonomies. Keep `taxonomy_id`
    immutable after a taxonomy has trace or lineage references; change
    `display_name` for a safer user-facing rename.

## ⚠️ What not to customize lightly

These Python modules are deliberately behavioral:

| Area | Why it should stay Python |
|---|---|
| `adamast/core/lifecycle.py` | owns session boundaries and generation/refinement trigger timing |
| `adamast/core/program.py` | owns lock-coordinated program state and pending traces |
| `adamast/core/traces.py` | owns canonical trace persistence and integration into taxonomy trace folders |
| `adamast/core/reflection.py` | parses the reflection contract from model text |
| `adamast/protocol/gate.py` | validates final-gate shape and retry envelope |
| `adamast/judges/*/schema.py`, validators, and controllers | enforce output structure after model calls |
| `adamast/hosts/*/runtime.py` | adapts host events to runtime calls |

!!! warning
    If you change these, run the full test suite.

## ✅ Verification after customization

Run at least:

```bash
python -m compileall adamast
python -m pytest -q
git diff --check
```

For packaging-sensitive changes to assets:

```bash
python -m pip wheel . --no-deps -w dist-check
```

Then inspect the wheel and confirm your new asset files are included.

## ➡️ Continue with

- [Architecture](ARCHITECTURE.md) — which module owns each behavior you might
  be tempted to change.
- [Complete runtime example](EXAMPLE_RUN.md) — the exact shapes your prompt
  changes must keep parseable.
