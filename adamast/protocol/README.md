# adamast/protocol/

The one compact-checkpoint implementation and the pre-submission gate. Both
hosts (Claude Code and Codex) and the single-LLM runtime speak exactly this
protocol — there is deliberately no per-host copy.

## Programs

| File | Purpose |
|---|---|
| [`checkpoint.py`](checkpoint.py) | Compact checkpoint fields: parsing, validation against the active taxonomy, and status derivation (`READY_TO_SUBMIT` / `REPAIR_REQUIRED`) |
| [`checkpoint_prompt.py`](checkpoint_prompt.py) | Rendering the checkpoint reflection instructions sent to the agent |
| [`gate.py`](gate.py) | The pre-submission gate: final-status parsing, the retry envelope, and re-prompt pinning |

The natural-language protocol text lives in [`assets/`](assets/)
(`checkpoint_reflection.md`, `pre_submission_protocol.md`, `format_repair.md`).
