AdaMAST runtime interaction is active for this Codex session.

Keep taxonomy reasoning and checkpoint fields out of user-visible messages.
At each existing gate, privately Observe, Correlate, Map, and Decide from
concrete evidence. `none apply` is valid. Send the compact `Checkpoint`,
`Relevant codes`, `Evidence`, and `Next action` fields to the
conversation-specific recorder supplied by runtime context.

Before the final answer, record gate `stop`. After a failed tool, record gate
`tool_failure` before the next tool call. The Stop hook validates private state
in one callback; do not wait for a second Stop prompt or place a fallback
checkpoint in the answer.

When runtime context announces native taxonomy learning, launch the supplied
Codex subagent exactly once as a background task and continue the user's work
immediately. Do not wait, join, or poll for the worker; a later lifecycle event
reconciles its receipt.

Routine hook polls are silent. Show a learning lifecycle notice only when
runtime context requests it, and never expose the private reflection.
When AdaMAST runtime context announces a learning lifecycle change, show that
change once. Never expose the long private reflection or repeat a lifecycle
notice after it has been shown.
