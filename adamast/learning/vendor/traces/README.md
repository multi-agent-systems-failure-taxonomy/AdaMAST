# adamast/learning/vendor/traces/

Trace loading + normalization + behavioral signal extraction for the
AdaMAST pipeline. Accepts multiple input formats and converts everything to
the canonical `UnifiedTrace` schema before generation or classification.

Auto-detected formats:

- Already-unified AdaMAST records (the canonical shape)
- tau-bench (`{traj, task_id, reward}`)
- Codex CLI sessions (`{type: session_meta | response_item | turn_context | event_msg}`)
- **Claude Code stream-json** (`claude --output-format stream-json` output;
  detected by lines with `type` in `{system, assistant, user, result}`)
- Event logs (`{event: ...}` lines)
- Conversation / Forgecode (`{messages: [{role, content}, ...]}`)
- KIRA trajectories (step dicts with `step_id` and `tool_calls`/`observation`)
- Plain text fallback (treated as one raw trajectory string)

## Programs

| File | Purpose |
|---|---|
| [`__init__.py`](__init__.py) | Public API: `load_traces`, `normalize_trace`, `extract_signals` |
| [`loader.py`](loader.py) | `TraceLoader`: file/dir/iterable loader with format auto-detection |
| [`normalizer.py`](normalizer.py) | Per-format converters that map raw traces into the unified AdaMAST schema |
| [`signals.py`](signals.py) | LLM-free behavioral signal extraction: truncation, looping, refusal, tool-failure patterns |
