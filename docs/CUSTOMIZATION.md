# AdaMAST customization guide

AdaMAST is split so users can change public behavior without touching runtime
controllers whenever possible.

Use this rule of thumb:

- edit Markdown when you want different model-facing wording;
- edit JSON when you want different declarative policy or defaults;
- edit Python only when you want different control flow, validation, storage,
  or integration behavior.

## Safe customization map

| Goal | Edit |
|---|---|
| Change checkpoint reflection wording | `adamast/protocol/assets/checkpoint_reflection.md` |
| Change final-gate protocol | `adamast/protocol/assets/pre_submission_protocol.md` and, for Claude Code, `adamast/hosts/claude_code/assets/final_gate_tail.md` |
| Change Claude Code standing instruction | `adamast/hosts/claude_code/assets/standing_prompt.md` |
| Change single-LLM standing instruction | `adamast/hosts/single_llm/assets/standing_prompt.md` |
| Change simple judge prompts | `adamast/judges/assets/` |
| Change Reflection Judge staged prompts | `adamast/judges/reflection_judge/assets/` |
| Change refinement prompt wording | `adamast/assets/standard_refinement_prompt.md`, `reflection_refiner_system.md`, `reflection_refiner_user.md`, `refinement_support_judge.md`, and `refinement_repair.md` |
| Change taxonomy-generation prompt wording | `vendor/adamast/pipeline/assets/` |
| Change classifier prompt wording | `vendor/adamast/assets/classifier_system.md` and `vendor/adamast/assets/classifier_user.md` |
| Change recognized model context profiles | `adamast/assets/model_profiles.json` |
| Change Claude Code event lists | `adamast/hosts/claude_code/assets/hook_events.json` |
| Change Reflection Judge schema enums | `adamast/judges/reflection_judge/assets/schema_enums.json` |
| Change generation seed role definitions | `vendor/adamast/pipeline/assets/role_definitions.json` |
| Change checker keyword lists | `vendor/adamast/pipeline/assets/checker_terms.json` |
| Change config-file validation | `adamast/assets/adamast_config.schema.json` plus Python config loaders |

## Config file first

For most deployments, prefer an `adamast.json` checked into the project rather
than long command lines:

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

Explicit CLI flags override config values. Unknown config fields are rejected
so misspellings do not silently change behavior.

## Hook customization

Claude Code has two hook layers:

1. built-in hooks, configured with `claude_code.built_in_hooks`;
2. custom hooks, configured with `adamast-claude-add-hook` or the
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
adamast-claude-add-hook \
  --project-dir . \
  --name pre-bash \
  --event PreToolUse \
  --matcher Bash \
  --command-pattern "python .*eval" \
  --checkpoint-key fixed \
  --mode blocking
```

Use `blocking` when the agent must satisfy the reflection contract before
continuing. Use `advisory` when the agent should receive a nudge but the host
should not block.

`matcher` is the host tool/event matcher, usually tool-name granularity such
as `Bash`. `command_pattern` is an optional regex against `tool_input` /
command text, so one Bash hook can fire only for a recurring command. Use
`checkpoint_key` to control how repeated firings close:

| Value | Use |
|---|---|
| `tool_use_id` | Default; each host tool call is independent. |
| `command` | Same command text shares a checkpoint key. |
| `fixed` | The hook name has one stable in-flight checkpoint. |

## Taxonomy customization

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
adamast-register-taxonomy --file taxonomy.json --id my-taxonomy-v1
```

Use it:

```bash
adamast-single-run --config adamast.json --inherit my-taxonomy-v1 --model gpt-5 --task "..."
```

`display_name`, `repo`, `domain`, and `summary` are display metadata. They do
not route, group, or select taxonomies. Keep `taxonomy_id` immutable after a
taxonomy has trace or lineage references; change `display_name` for a safer
user-facing rename.

## What not to customize lightly

These Python modules are deliberately behavioral:

| Area | Why it should stay Python |
|---|---|
| `adamast/lifecycle.py` | owns session boundaries and generation/refinement trigger timing |
| `adamast/program.py` | owns lock-coordinated program state and pending traces |
| `adamast/traces.py` | owns canonical trace persistence and integration into taxonomy trace folders |
| `adamast/reflection.py` | parses the reflection contract from model text |
| `adamast/protocol.py` | validates final-gate shape and retry envelope |
| `adamast/judges/*/schema.py`, validators, and controllers | enforce output structure after model calls |
| `adamast.hosts/*/runtime.py` | adapts host events to runtime calls |

If you change these, run the full test suite.

## Verification after customization

Run at least:

```bash
python -m compileall adamast adamast.hosts finding adamast.judges vendor
python -m pytest -q
git diff --check
```

For packaging-sensitive changes to assets:

```bash
python -m pip wheel . --no-deps -w dist-check
```

Then inspect the wheel and confirm your new asset files are included.
