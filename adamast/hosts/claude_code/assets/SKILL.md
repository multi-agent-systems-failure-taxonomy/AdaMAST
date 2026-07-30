---
name: adamast-failure-modes
description: Use when working on an agent task where AdaMAST failure-mode checkpoints, final submission gates, trace capture, taxonomy generation/refinement, or AdaMAST CLI setup should guide Claude Code. This skill helps Claude apply AdaMAST during software or research tasks, diagnose its own trajectory against the active taxonomy, and avoid claiming completion before the AdaMAST final gate is satisfied.
---

# AdaMAST for Claude Code

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
  fields privately and send them to the recorder command prefix supplied by
  runtime context. Do not print either the compact block or the longer
  reflection in the conversation.

## If the AdaMAST hooks are installed

The hooks validate recorded gates and keep their blocking behavior when repair
is required, so the recorder is the only way past a final gate. Prefer the
package CLIs over hand-rolled state:

- Use `adamast doctor --claude-code` to check setup.
- Use `adamast find --list` or `adamast find --inherit <taxonomy_id>` to resolve stored taxonomies.
- Use `adamast dashboard` to inspect recorded evidence and fired codes.
- Use `adamast traces status` to inspect trace growth.

Runtime context supplies an absolute path for the recorder. Use exactly that
prefix rather than the bare console-script name, which is often not on `PATH`.

If a command asks for `--trace-output`, use the project-specific trace folder supplied by the user or `./adamast-program` for local experiments.

## If no AdaMAST command is available

Without the package there is no trace capture, taxonomy learning, or dashboard.
Still follow the AdaMAST final-gate shape in the final reasoning pass:

- `Final AdaMAST status:` `READY_TO_SUBMIT` or `REPAIR_REQUIRED`
- `Codes checked:` relevant taxonomy ids, or none
- `Evidence:` concrete task or verification evidence
- `Repair attempts used:` integer count
- `Final decision:` submit, repair, or report unresolved

Do not expose private chain-of-thought. Keep the final user-facing answer concise; mention only the actionable result and verification status.
