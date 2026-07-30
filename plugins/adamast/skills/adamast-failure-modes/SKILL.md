---
name: adamast-failure-modes
description: Use when working on a Claude Code or Codex task where AdaMAST failure-mode checkpoints, final submission gates, trace capture, or taxonomy generation/refinement should guide the agent. This skill helps the host apply AdaMAST, diagnose its own trajectory against the active taxonomy, and avoid claiming completion before the AdaMAST final gate is satisfied.
---

# AdaMAST for Claude Code and Codex

Use AdaMAST as a lightweight runtime discipline while doing the user's task.

## Runtime behavior

- Keep the active taxonomy out of startup context unless the user or an AdaMAST command explicitly supplies it.
- At meaningful boundaries, inspect the recent trajectory before continuing:
  - finishing a sub-task;
  - recovering from a failed tool command;
  - returning from a subagent;
  - switching strategy;
  - preparing to submit a final answer.
- Analyze only activity since the previous AdaMAST checkpoint. Use the reflection
  order from AdaMAST prompts:
  1. Observe concrete events or missing expected steps.
  2. Correlate only evidence-supported causes.
  3. Map to taxonomy codes only when evidence supports the match.
  4. Decide whether to make one focused repair or continue.
- Treat `none apply` as valid. Do not invent a failure mode or force an edit.
- Before final submission, complete a final AdaMAST gate and only report ready when no unresolved taxonomy-relevant issue remains.
- Create the compact `Checkpoint`, `Relevant codes`, `Evidence`, and `Next action`
  fields privately and send them to the conversation-specific recorder supplied
  by runtime context. Do not print either the compact block or the longer
  reflection in the conversation.

## If the AdaMAST package is available

When the plugin hooks are active the package is already installed privately,
and the hooks validate recorded gates and retain their blocking behavior when
repair is required. The native plugin does not modify the user's shell `PATH`.
If the user separately installed the package CLI and `adamast` is on `PATH`,
prefer its commands over hand-rolled state:

- Use `adamast doctor --claude-code` or `adamast doctor --codex` to check setup.
- Use `adamast find --list` or `adamast find --inherit <taxonomy_id>` to resolve stored taxonomies.
- Use the absolute checkpoint-recorder command supplied by runtime context to
  record a compact checkpoint outside chat.
- Use `adamast dashboard` to inspect recorded evidence and fired codes.
- Use `adamast traces status` to inspect trace growth.

If a command asks for `--trace-output`, use the project-specific trace folder supplied by the user or `./adamast-program` for local experiments.

## If no AdaMAST command is available

The plugin degrades to guidance only: no trace capture, no taxonomy learning, no
dashboard. Still follow the AdaMAST final-gate shape in the final reasoning pass:

- `Final AdaMAST status:` `READY_TO_SUBMIT` or `REPAIR_REQUIRED`
- `Codes checked:` relevant taxonomy ids, or none
- `Evidence:` concrete task or verification evidence
- `Repair attempts used:` integer count
- `Final decision:` submit, repair, or report unresolved

Do not expose private chain-of-thought. Keep the final user-facing answer concise; mention only the actionable result and verification status.
