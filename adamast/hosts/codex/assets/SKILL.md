---
name: adamast-failure-modes
description: Use when working on an agent task where AdaMAST failure-mode checkpoints, final submission gates, trace capture, taxonomy generation/refinement, or AdaMAST CLI setup should guide Codex. This skill helps Codex apply AdaMAST during software or research tasks, diagnose its own trajectory against the active taxonomy, and avoid claiming completion before the AdaMAST final gate is satisfied.
---

# AdaMAST for Codex

Use AdaMAST as a lightweight runtime discipline while doing the user's task.

## Runtime behavior

- Keep the active taxonomy out of startup context unless the user or an AdaMAST command explicitly supplies it.
- At meaningful boundaries, inspect the recent trajectory before continuing:
  - finishing a sub-task;
  - recovering from a failed tool command;
  - switching strategy;
  - preparing to submit a final answer.
- Use the reflection order from AdaMAST prompts:
  1. Observe concrete events or missing expected steps.
  2. Correlate only evidence-supported causes.
  3. Map to taxonomy codes only when evidence supports the match.
  4. Decide whether to make one focused repair or continue.
- Treat `none apply` as valid. Do not invent a failure mode or force an edit.
- When the SessionStart context says taxonomy selection is pending, show the
  supplied selector verbatim and do no task work until the user chooses. After
  selection, resume any held task without asking the user to repeat it.
- When the selector reports `No taxonomy`, do not emit AdaMAST checkpoints or
  describe AdaMAST as active for that conversation.
- Before final submission, complete a final AdaMAST gate and only report ready when no unresolved taxonomy-relevant issue remains.
- When runtime context announces an `AdaMAST native taxonomy learning` job,
  immediately launch exactly one native Codex subagent with the supplied task
  prompt as a background task. Continue the user's main work immediately. Do
  not wait, join, or poll for the worker. Do not perform the
  taxonomy job in the main agent, invoke `codex exec`, request an API key, or
  recursively launch another taxonomy agent. The subagent must return the
  supplied receipt envelope exactly; AdaMAST validates and activates it.
- After any failed tool call, privately map the evidence to the active taxonomy
  and, before the next tool call, send one concise `tool_failure` checkpoint to
  the conversation-specific recorder supplied by runtime context. Do not print
  it in the conversation. Use `none apply` when the failure was intentional or
  harmless; do not invent a failure code.
- Keep routine hook polls silent. Show each learning lifecycle notice once when
  runtime context requests it, and keep the longer reflection private.
- Before each substantive Codex final answer, send the compact checkpoint
  required by active runtime context to the recorder with gate `stop`:
  `Checkpoint`, `Relevant codes`, `Evidence`, and `Next action`. Do not append
  the block to the answer. Keep the longer Observe/Correlate/Map/Decide
  reflection internal unless a hook explicitly requests it.

## If the AdaMAST package is available

Prefer the package CLIs over hand-rolled state:

- Use `adamast-doctor --config adamast.json` to check setup.
- Use `adamast-find --list` or `adamast-find --inherit <taxonomy_id>` to resolve stored taxonomies.
- Use `adamast-single-run` for no-harness single-model tasks.
- Use `adamast-dashboard` to inspect recorded evidence and fired codes.
- Use `adamast-traces status` to inspect trace growth.

If a command asks for `--trace-output`, use the project-specific trace folder supplied by the user or `./adamast-program` for local experiments.

## If no AdaMAST command is available

Perform the same Observe/Correlate/Map/Decide check privately, but do not claim
that a runtime checkpoint was recorded. Never append a checkpoint, taxonomy
codes, evidence block, or final-gate status to the user-facing answer. Mention
only an actionable recorder/setup failure when it affects the requested work.
