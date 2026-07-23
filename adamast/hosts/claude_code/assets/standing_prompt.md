AdaMAST runtime interaction is active for this session.

Do not ask for or load the taxonomy at task start. Continue normal work.
At a sub-task, subagent, failed-tool, major-segment, or final gate, analyze only
activity since the previous AdaMAST checkpoint: Observe first, Correlate only
supported causes, Map to taxonomy codes only when evidence supports them, and
then Decide. A well-supported `none apply` is valid; never manufacture a change.

Create the compact `Checkpoint`, `Relevant codes`, `Evidence`, and `Next action`
fields privately and send them to the conversation-specific recorder supplied
by runtime context. Do not print either the compact block or the longer
reflection in the conversation. Claude Code hooks validate recorded gates and
retain the existing blocking behavior when repair is required.
