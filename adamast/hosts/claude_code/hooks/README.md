# adamast/hosts/claude_code/hooks/

One file per Claude Code hook event. Each file exports a single `handle`
function (`(event, config) -> (exit_code, payload)`) that the
[`dispatcher`](../dispatcher.py) routes to. The implementations are thin
shims; all real behavior lives in [`../runtime.py`](../runtime.py), so the
hook files stay a 1-to-1 map of the Claude Code hook surface.

## Programs

| File | Hook event | Blocking? | What it does |
|---|---|---|---|
| [`__init__.py`](__init__.py) | none | none | Package marker |
| [`session_start.py`](session_start.py) | `SessionStart` | no | Pick & hold the session taxonomy; return the standing-instruction additionalContext |
| [`user_prompt_submit.py`](user_prompt_submit.py) | `UserPromptSubmit` | yes | Resolve taxonomy selection: hold the first substantive prompt until the picker choice lands, then continue it |
| [`session_end.py`](session_end.py) | `SessionEnd` | no | Persist pending traces; trigger generation/refinement; release dashboard |
| [`task_completed.py`](task_completed.py) | `TaskCompleted` | yes | Sub-task checkpoint: enforce a reflection block in the transcript, block until satisfied |
| [`subagent_stop.py`](subagent_stop.py) | `SubagentStop` | yes | Sub-agent checkpoint: same shape as `task_completed` but on the agent transcript |
| [`stop.py`](stop.py) | `Stop` | yes | Main blocking final-gate reflection (the "pre-submission" AdaMAST gate) |
| [`post_tool_use.py`](post_tool_use.py) | `PostToolUse` | no | Reactive nudge on a suspicious successful tool result |
| [`post_tool_use_failure.py`](post_tool_use_failure.py) | `PostToolUseFailure` | no | Reactive nudge on a failed tool call |
