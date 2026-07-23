# Integrating AdaMAST into a pipeline

Put AdaMAST inside an agent harness you own. This page gives you the ownership
boundary and the minimum call sequence for runtime reflection, trace capture,
generation, refinement, and lineage.

!!! tip "Prefer a ready-made integration?"
    Start from `adamast/hosts/claude_code/` (Claude Code) or
    `adamast/hosts/single_llm/` (single LLM call).

## 🧭 The boundary

| AdaMAST owns | Your harness owns |
|---|---|
| Taxonomy selection by `taxonomy_id` | Model execution |
| The built-in MAST fallback when no taxonomy is inherited | When a task/subtask/checkpoint boundary occurs |
| The pre-submission gate parser and retry envelope | How checkpoint prompts reach the agent |
| Canonical trace persistence | How the complete task trajectory is collected |
| Taxonomy generation/refinement triggers | Any trace redaction/summarization before calling `record_trace` |
| Taxonomy storage and successor lineage | User-facing configuration and credentials |
| The optional local dashboard | |

!!! note "`trace_output` is the program identity"
    `trace_output` is mandatory. Reusing the same `trace_output` means "same
    program": counters, pending traces, active taxonomy, and local manifest
    state are shared. Use a different `trace_output` when two task streams
    should learn independently.

## 🔁 Minimal lifecycle

A checkpoint — also called a gate — is any boundary where the harness asks the
agent to reflect before continuing; the final gate is the blocking checkpoint
before an answer is released.

```python
from adamast import (
    GenerationTrace,
    end_session,
    pre_submission,
    record_trace,
    start_session,
)

session = start_session(
    trace_output="./adamast-program",
    adamast_model="gpt-5",
    inherit=None,  # omit this argument entirely for the default MAST path
)

# At task start:
# - deliver session.delivery.runtime_protocol as standing behavior
# - do NOT dump the full taxonomy into ordinary task context

# At a meaningful checkpoint:
# - deliver session.delivery.taxonomy plus your recent trajectory window
# - ask the agent to produce the required AdaMAST reflection shape
#   (Observe -> Correlate -> Map -> Decide)
# - continue only after your harness accepts/records that reflection

# Before releasing a final answer:
decision = pre_submission(session, gate_text)
if not decision.allow:
    # ask the agent to repair or re-emit a valid gate response
    ...

record_trace(
    session,
    GenerationTrace(
        problem_id="stable-task-id",
        task="user-visible task prompt",
        raw_trajectory="complete redacted trajectory",
        metadata={"harness": "my-pipeline"},
    ),
)

result = end_session(session)
```

`end_session()` is where learning triggers fire. If the active taxonomy is
MAST and the program has reached the generation threshold, generation starts
or runs. If the active taxonomy is a stored taxonomy and the program has
reached its refinement threshold, refinement starts or runs.

## 🧬 Taxonomy inheritance semantics

All harnesses should preserve the same selection behavior:

| User input | Runtime selection |
|---|---|
| no inherit value at all | start from built-in MAST |
| explicit `taxonomy_id` | inherit that stored taxonomy |
| explicit picker request, e.g. `--inherit-pick` | open the blocking local picker |

Bare `--inherit` is still accepted by the bundled CLIs as a deprecated
compatibility alias for the picker. New harnesses should expose an explicit
picker flag instead of overloading the missing-id case.

Repository and domain are display metadata only. They must not route taxonomy
selection.

## 🪝 Claude Code hook selection

`adamast claude install` installs every built-in hook by default. Reduce noise
with `--disable-hook`, `--post-tool-use-matchers`, and
`--post-tool-use-failure-matchers`, or with the equivalent config field
`claude_code.built_in_hooks`: boolean values enable or disable an event, lists
set matchers for `PostToolUse` and `PostToolUseFailure`, and object values
carry both `enabled` and `matchers`. Full install examples live in the
[Claude Code guide](CLAUDE_CODE.md).

## ⚙️ Runtime configuration surface

AdaMAST supports a dependency-free `adamast.json` config file for shared runtime
values:

```json
{
  "trace_output": "./adamast-program",
  "adamast_model": "gpt-5"
}
```

Unknown fields are rejected and explicit CLI/API arguments win over
config-file values; every field, default, precedence rule, and
path-resolution rule is defined in [CONFIGURATION.md](CONFIGURATION.md).

Set `evidence_export` when a pipeline needs a durable JSON snapshot outside the
program trace folder. `adamast status` reads the program manifest and reports the
learning-call usage ledger; unavailable provider token/cost data is reported as
unavailable, not estimated.

Every supported operational CLI accepts the same file with
`--config adamast.json`: `adamast claude install`, `adamast codex install`,
`adamast single-run`, `adamast import-traces`, `adamast register-taxonomy`,
`adamast doctor`, and `adamast traces status|export|prune`. Custom harnesses
can load the same file with `adamast.load_adamast_config`.

Use `adamast doctor` in installation flows to verify the resolved values:

```bash
adamast doctor \
  --config adamast.json \
  --trace-output ./adamast-program \
  --adamast-model gpt-5 \
  --dashboard-port 0
```

## 🔑 Model and credential expectations

`adamast_model` is the model used by AdaMAST generation, judging, and refinement.
Keep it separate from the task agent's model if your pipeline exposes both.

Transport selection is model-id based:

| Model ID shape | Transport |
|---|---|
| Claude / Anthropic / Bedrock-shaped | Anthropic transports unless `OPENAI_BASE_URL` is set |
| Gemini-shaped | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| OpenAI-shaped | OpenAI client; honors `OPENAI_BASE_URL` |

!!! warning "Never store credential values"
    Do not write credential values into AdaMAST config. Store only environment
    variable names or let the provider SDK read its normal environment.

## 🔒 Trace privacy and redaction

AdaMAST stores traces and may send trace excerpts to the AdaMAST model for
generation, judging, and refinement. A harness should redact secrets before
calling `record_trace()`. At minimum, redact:

- API keys, bearer tokens, and cookies;
- private file paths if they are sensitive;
- user/private data not needed to understand the failure pattern;
- benchmark labels or outcomes if they would leak oracle information.

The bundled adapters (Claude Code, Codex, single-LLM) apply the shipped
conservative redactor by default; set `redact_traces: false` to opt out.
Custom harnesses remain responsible for their own redaction, including any
project-specific patterns.

!!! warning "Redaction is a harness responsibility"
    The runtime strips known outcome fields from learning inputs, but it
    cannot guess secrets inside `raw_trajectory`.

AdaMAST ships a small helper for common credential shapes:

```python
from adamast import GenerationTrace, redact_trace, record_trace

trace = GenerationTrace(
    problem_id="task-1",
    task=task_prompt,
    raw_trajectory=full_transcript,
    metadata={"harness": "my-pipeline"},
)

record_trace(session, redact_trace(
    trace,
    extra_patterns=[r"internal-ticket-\d+"],
))
```

This helper is intentionally conservative and dependency-free. It is not a
data-loss-prevention product; use project-specific `extra_patterns` for values
your pipeline knows are sensitive.

## 🧰 Operational commands to expose to users

Useful commands for pipeline installers:

```bash
adamast doctor --trace-output ./adamast-program --adamast-model gpt-5
adamast find --list
adamast traces status --trace-output ./adamast-program
adamast traces export --taxonomy-id <taxonomy_id> --output traces.jsonl
adamast traces prune --older-than-days 90 --taxonomy-id <taxonomy_id>
```

!!! note
    `adamast traces prune` is dry-run by default; it requires `--yes` for
    deletion.

## ➡️ Continue with

- [Native taxonomy learning](NATIVE_LEARNING.md) — the job protocol behind
  the learning triggers.
- [Configuration](CONFIGURATION.md) — every runtime field the harness can
  set.
