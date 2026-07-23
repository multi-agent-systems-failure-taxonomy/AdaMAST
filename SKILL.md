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
- Before final submission, complete a final AdaMAST gate and only report ready when no unresolved taxonomy-relevant issue remains.
- In an interactive Codex conversation, use the conversation-specific
  `adamast-codex-checkpoint` recorder supplied by runtime context. Send the
  existing four gate fields there before the final answer instead of printing
  them in the conversation.

## If the AdaMAST package is available

Prefer the package CLIs over hand-rolled state:

- Use `adamast-doctor --config adamast.json` to check setup.
- Use `adamast-find --list` or `adamast-find --inherit <taxonomy_id>` to resolve stored taxonomies.
- Use `adamast-single-run` for no-harness single-model tasks.
- Use `adamast-dashboard` to inspect recorded evidence and fired codes.
- Use `adamast-traces status` to inspect trace growth.

If a command asks for `--trace-output`, use the project-specific trace folder supplied by the user or `./adamast-program` for local experiments.

## If no AdaMAST command is available

Still follow the AdaMAST final-gate shape in the final reasoning pass:

- `Final AdaMAST status:` `READY_TO_SUBMIT` or `REPAIR_REQUIRED`
- `Codes checked:` relevant taxonomy ids, or none
- `Evidence:` concrete task or verification evidence
- `Repair attempts used:` integer count
- `Final decision:` submit, repair, or report unresolved

Do not expose private chain-of-thought. Keep the final user-facing answer concise; mention only the actionable result and verification status.
