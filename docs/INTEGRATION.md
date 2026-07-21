# Integrating AdaMAST into a pipeline

This is the harness-author contract: what your application owns, what AdaMAST
owns, and the minimum call sequence needed to get runtime reflection,
trace capture, generation, refinement, and lineage.

If you only want a ready-made integration, start with:

- Claude Code: [`adamast_integration/claude_code/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/blob/main/adamast_integration/claude_code/)
- Single LLM call: [`adamast_integration/single_llm/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/blob/main/adamast_integration/single_llm/)

## The boundary

AdaMAST owns:

- taxonomy selection by `taxonomy_id`;
- the built-in MAST fallback when no taxonomy is inherited;
- the pre-submission gate parser and retry envelope;
- canonical trace persistence;
- taxonomy generation/refinement triggers;
- taxonomy storage and successor lineage;
- the optional local dashboard.

Your harness owns:

- model execution;
- when a task/subtask/checkpoint boundary occurs;
- how checkpoint prompts reach the agent;
- how the complete task trajectory is collected;
- any trace redaction/summarization before calling `record_trace`;
- user-facing configuration and credentials.

`trace_output` is mandatory because it is the program identity. Reusing the
same `trace_output` means "same program": counters, pending traces, active
taxonomy, and local manifest state are shared. Use a different `trace_output`
when two task streams should learn independently.

## Minimal lifecycle

```python
from adamast_runtime import (
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

## Taxonomy inheritance semantics

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

## Claude Code hook selection

`adamast-claude-install` installs every built-in hook by default for backwards
compatibility. Projects can reduce noise by disabling a built-in event or by
restricting tool-result hooks to selected Claude Code tool matchers:

```bash
adamast-claude-install \
  --config adamast.json \
  --disable-hook SubagentStop \
  --post-tool-use-matchers Bash,Edit \
  --post-tool-use-failure-matchers Bash
```

The equivalent config-file field is `claude_code.built_in_hooks`. Boolean
values enable or disable an event; lists set matchers for `PostToolUse` and
`PostToolUseFailure`; object values can carry both `enabled` and `matchers`.

## Runtime configuration surface

AdaMAST supports a dependency-free `adamast.json` config file for shared runtime
values:

```json
{
  "trace_output": "./adamast-program",
  "adamast_model": "gpt-5"
}
```

Every field, its default, and the precedence rules are defined in
[CONFIGURATION.md](CONFIGURATION.md). Relative paths are resolved relative to
the config file. Unknown fields are rejected so spelling mistakes fail loudly.
Explicit CLI/API arguments win over config-file values.

Set `evidence_export` when a pipeline needs a durable JSON snapshot outside the
program trace folder. `adamast-status` reads the program manifest and reports the
learning-call usage ledger; unavailable provider token/cost data is reported as
unavailable, not estimated.

Supported operational CLIs:

- `adamast-claude-install --config adamast.json`
- `adamast-single-run --config adamast.json`
- `adamast-import-traces --config adamast.json`
- `adamast-register-taxonomy --config adamast.json`
- `adamast-doctor --config adamast.json`
- `adamast-traces status|export|prune --config adamast.json`

Custom harnesses can load the same file with `adamast_runtime.load_adamast_config`.

Use `adamast-doctor` in installation flows to verify the resolved values:

```bash
adamast-doctor \
  --config adamast.json \
  --trace-output ./adamast-program \
  --adamast-model gpt-5 \
  --dashboard-port 0
```

## Model and credential expectations

`adamast_model` is the model used by AdaMAST generation, judging, and refinement.
Keep it separate from the task agent's model if your pipeline exposes both.

Transport selection is model-id based:

- Claude / Anthropic / Bedrock-shaped IDs use Anthropic transports unless
  `OPENAI_BASE_URL` is set.
- Gemini-shaped IDs use `GEMINI_API_KEY` or `GOOGLE_API_KEY`.
- OpenAI-shaped IDs use the OpenAI client and honor `OPENAI_BASE_URL`.

Do not write credential values into AdaMAST config. Store only environment
variable names or let the provider SDK read its normal environment.

## Trace privacy and redaction

AdaMAST stores traces and may send trace excerpts to the AdaMAST model for
generation, judging, and refinement. A harness should redact secrets before
calling `record_trace()`.

At minimum, redact:

- API keys, bearer tokens, and cookies;
- private file paths if they are sensitive;
- user/private data not needed to understand the failure pattern;
- benchmark labels or outcomes if they would leak oracle information.

The bundled adapters (Claude Code, Codex, single-LLM) apply the shipped
conservative redactor by default; set `redact_traces: false` to opt out.
Custom harnesses remain responsible for their own redaction, including any
project-specific patterns.

The runtime strips known outcome fields from learning inputs, but it cannot
guess secrets inside `raw_trajectory`. Treat redaction as a harness
responsibility.

AdaMAST ships a small helper for common credential shapes:

```python
from adamast_runtime import GenerationTrace, redact_trace, record_trace

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

## Operational commands to expose to users

Useful commands for pipeline installers:

```bash
adamast-doctor --trace-output ./adamast-program --adamast-model gpt-5
adamast-find --list
adamast-traces status --trace-output ./adamast-program
adamast-traces export --taxonomy-id <taxonomy_id> --output traces.jsonl
adamast-traces prune --older-than-days 90 --taxonomy-id <taxonomy_id>
```

`adamast-traces prune` is dry-run by default; require `--yes` for deletion.
