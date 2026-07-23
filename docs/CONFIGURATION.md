# Configuration reference

Every `adamast.json` field, default, and precedence rule is defined on this
page — it is the canonical reference. Other pages show only the fields they
need.

## 📥 How loading works

- AdaMAST reads one dependency-free JSON file, `adamast.json` by default, or
  the path given by `--config`.
- Explicit CLI/API arguments win over config-file values.
- Unknown fields are rejected so spelling mistakes fail loudly.
- Relative paths are resolved relative to the config file; `~` is expanded.
- `"version"` must be `1` (or omitted).

!!! warning "Credentials"
    AdaMAST never stores credential values. Set provider keys in your
    environment.

## 🪶 Minimal config

Most projects only need this:

```json
{
  "version": 1,
  "trace_output": "./adamast-program",
  "adamast_model": "gpt-5"
}
```

Everything else has a sensible default.

## 🧱 Core fields

| Field | Default | Purpose |
|---|---|---|
| `trace_output` | required | Program-specific folder for traces, evidence, and manifest state. This is the program identity: same folder = same program. |
| `adamast_model` | required for learning | Model used by AdaMAST generation, judging, and refinement. Keep it separate from your task-solving model. |
| `trace_root` | `~/.adamast/traces` | Root for per-taxonomy trace folders after acceptance. |
| `store_dir` | `~/.adamast/taxonomies` | Flat taxonomy store: one JSON file per `taxonomy_id`. |
| `inherit` | unset | `taxonomy_id` to start from. Unset (or the string `"none"`) starts from built-in MAST. |
| `dashboard` | `true` | Let integrations launch the localhost dashboard automatically. |
| `evidence_export` | unset | Optional external snapshot sink for session-end evidence. A `.json` value is written as that exact file; any other value is treated as a directory receiving one `<program_id>.json` per program. |
| `redact_traces` | `true` | First-party adapters strip credential-looking substrings (API keys, tokens, cookies) from traces before persistence. Disable only when traces are known clean and exact text matters. |

## 📈 Learning fields

| Field | Default | Purpose |
|---|---|---|
| `generation_threshold` | `5` | Warm-up trace count before generating a taxonomy from MAST. Raise it if early traces are noisy or unrepresentative. |
| `generation_stops` | `false` | If `true`, the current task waits for generation to finish; if `false`, running tasks continue on MAST and the result activates afterwards. |
| `skip_judge` | `false` | Skip Reflection-Judge cleanup of generated taxonomies (structural validation still runs). |
| `k_init` | `10` | Traces required before the first refinement of an active stored taxonomy. |
| `k` | `20` | Traces required between later refinements for the same program. |
| `refinement_stops` | `false` | If `true`, the current task waits for refinement to finish. |
| `advanced_refinement` | `false` | Add one support-judge repair pass during refinement. |
| `freeze` | `false` | Inference-only mode: record traces and evidence but skip generation and refinement. Use for pinned-taxonomy A/B evaluations. |

!!! note "Hook hosts always learn in the background"
    Hook integrations (Claude Code, Codex) always run generation and
    refinement in background workers regardless of `generation_stops` /
    `refinement_stops`: a hook process is killed at the harness's per-hook
    timeout, so inline learning is honored only by the single-LLM and CLI
    paths that own their process.

See [TRACES_AND_LEARNING.md](TRACES_AND_LEARNING.md) for how these interact.

## 🚦 Gate fields

These fields budget the runtime checkpoints — the reflection boundaries also
called gates — and the final pre-submission checkpoint. Form failures
(unparseable reflections) and substantive repairs draw on separate budgets, so
tuning one loop cannot silently disable the other.

| Field | Default | Purpose |
|---|---|---|
| `repair_rounds` | `3` | Substantive `REPAIR_REQUIRED` repair opportunities at the final gate before honest unresolved release. |
| `format_retries` | `2` | Reflection parse/shape failures tolerated per checkpoint cycle before the gate releases. Format re-prompts are targeted: they name the missing elements and pin the previous verdict. |
| `max_retries` | `3` | Legacy shared knob, accepted indefinitely: maps to `repair_rounds` when `repair_rounds` is not set. |
| `gate_exhaustion_policy` | `"raise"` | Single-LLM only. `"raise"` errors when the repair cap is hit; `"release"` returns the best answer and records `gate_allowed=false` (useful for benchmark wrappers). |

## 🔌 Model transport fields

Transport is selected by model-id shape; these fields adjust it:

| Field | Default | Purpose |
|---|---|---|
| `model` | unset | Task-solving model for `adamast single-run` (same as its `--model` flag). Not used by learning calls. |
| `openai_base_url` | unset | OpenAI-compatible endpoint override; also honored via the `OPENAI_BASE_URL` environment variable. |
| `openai_api_key_env` | unset | Name of the environment variable holding the key for that endpoint. Store the variable *name*, never the value. |

Credential expectations per provider are covered in
[INSTALLATION.md](INSTALLATION.md).

## 🧩 Integration-scoped fields

| Field | Default | Purpose |
|---|---|---|
| `project_dir` | unset | Project directory for the install CLIs (same as `--project-dir`). |
| `claude_code` | unset | Claude Code adapter policy. `claude_code.built_in_hooks` enables/disables built-in events and sets tool matchers; see [CLAUDE_CODE.md](CLAUDE_CODE.md). |
| `codex` | unset | Codex adapter policy under `codex.hooks`; see [CODEX.md](CODEX.md). |
| `failure_throttle_calls` | `5` | Claude Code: minimum tool calls between repeated `PostToolUseFailure` nudges for the same failure. |
| `failure_recency_seconds` | `30` | Claude Code: minimum seconds between repeated failure nudges. |
| `recent_activity_messages` | `8` | Single LLM: trajectory-window size (messages) delivered at checkpoints. |
| `recent_activity_chars` | `12000` | Single LLM: trajectory-window size (characters) delivered at checkpoints. |

The legacy top-level `built_in_hooks`, `custom_hooks`, and `codex_hooks`
fields are still accepted as compatibility aliases for the scoped forms.

!!! note "User-level installs use different defaults"
    The defaults in the tables below describe explicit project installs.
    `adamast codex install --user-level` instead defaults to
    `project_scope: "auto"`, `session_selector: "prompt"`,
    `selector_surface: "browser"`, and `learning_backend: "codex_subagent"`.
    `adamast claude install --user-level` uses the parallel Claude values
    with `learning_backend: "claude_subagent"`. Both user-level commands
    default to the shared root `~/.adamast/interactive` and runtime identity
    `interactive-session`.

Codex user-level interactive hooks may also set:

| Field | Default | Purpose |
|---|---|---|
| `codex.project_scope` | `"explicit"` | `"auto"` derives a separate program from each canonical project root. |
| `codex.project_id` | unset | Optional stable identity override, useful for intentionally joined worktrees. |
| `codex.task_group` | `"default"` | Project-local trace, taxonomy, and refinement group. |
| `codex.session_selector` | `"off"` | Set to `"prompt"` to ask for MAST, a compatible stored taxonomy, or AdaMAST-off at the start of a new Codex conversation. |
| `codex.selector_surface` | `"browser"` | `"browser"` opens the session-bound local library from the first `UserPromptSubmit`; `"inline"` presents the same choices in chat. `SessionStart` never launches a new browser selector. |
| `codex.learning_backend` | `"provider"` | Set to `"codex_subagent"` for durable learning through a native subagent in the active Codex task, without a separate API key or CLI login. |
| `codex.worker_model` | unset | Legacy compatibility field; native spawned subagents use the active Codex task's model policy. |
| `codex.codex_cli_path` | unset | Legacy compatibility field; not required by native in-task learning. |
| `codex.worker_timeout_seconds` | `1800` | Claim lease for each native generation/refinement and support-review subagent. |

Claude Code accepts parallel interactive fields:

| Field | Default | Purpose |
|---|---|---|
| `claude_code.project_scope` | `"explicit"` | `"auto"` derives a program from the canonical project root. |
| `claude_code.project_id` | unset | Optional stable project identity override. |
| `claude_code.task_group` | `"default"` | Logical project-local taxonomy and refinement group. Automatic routing namespaces it for Claude Code, separately from Codex. |
| `claude_code.session_selector` | `"off"` | `"prompt"` asks for MAST, a compatible taxonomy, or AdaMAST-off. |
| `claude_code.selector_surface` | `"inline"` | `"browser"` opens the session-bound local library; user-level installs default to `"browser"`. |
| `claude_code.learning_backend` | `"provider"` | `"claude_subagent"` uses native generator and support-review Agent subtasks without a separate API key or CLI login. |
| `claude_code.worker_model` | unset | Legacy compatibility field; the native Agent follows the active session's model policy. |
| `claude_code.claude_cli_path` | unset | Legacy detached-worker compatibility field; native in-session learning does not use it. |
| `claude_code.worker_timeout_seconds` | `1800` | Browser-selection wait limit and claim lease for each native generation/refinement and support-review Agent. The installed browser `UserPromptSubmit` hook adds a 15-second shutdown margin. |

## 🏷️ Display metadata

| Field | Default | Purpose |
|---|---|---|
| `repo` | auto-discovered | Display-only repository label shown in the picker and dashboard. Never routes taxonomy selection. |
| `repo_path` | unset | Directory used to auto-discover the display repo label from git metadata. |

## ✅ Full example

```json
{
  "version": 1,
  "trace_output": "./adamast-program",
  "trace_root": "~/.adamast/traces",
  "store_dir": "~/.adamast/taxonomies",
  "adamast_model": "gpt-5",
  "inherit": null,
  "generation_threshold": 5,
  "generation_stops": false,
  "skip_judge": false,
  "k_init": 10,
  "k": 20,
  "refinement_stops": false,
  "advanced_refinement": false,
  "freeze": false,
  "repair_rounds": 3,
  "format_retries": 2,
  "dashboard": true,
  "claude_code": {
    "built_in_hooks": {
      "SubagentStop": false,
      "PostToolUse": ["Bash", "Edit"],
      "PostToolUseFailure": {
        "enabled": true,
        "matchers": ["Bash"]
      }
    }
  }
}
```

Validate any config with:

```bash
adamast doctor --config adamast.json
```

## ➡️ Continue with

- [Traces and learning](TRACES_AND_LEARNING.md) — how the learning fields
  interact at runtime.
- [Troubleshooting](TROUBLESHOOTING.md) — when a resolved value does not
  behave as expected.
