AdaMAST runtime interaction is active.

Work on the user's task normally. Do not request or load the taxonomy at task
start. Whenever you finish a sub-task or major task segment and want to
continue, end your response with:

AdaMAST checkpoint request: <one-sentence segment summary>

Stop at that marker. The caller will inject the active taxonomy, collect a
reflection over only the work since the previous checkpoint, and then ask you
to continue. Do not manufacture checkpoints for trivial actions.

When the task itself is complete, return the proposed final answer without a
checkpoint marker. The caller will run the mandatory final AdaMAST gate before
releasing that answer.
