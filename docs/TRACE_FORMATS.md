# Accepted trace formats

BASELINE accepts a `.json` or `.jsonl` file, or a directory containing those
files recursively. Every source is normalized before the first model call.

Use this command to validate without generating a taxonomy:

```powershell
adamast traces validate .\traces
```

To inspect exactly what generation will receive:

```powershell
adamast traces normalize .\traces --output .\traces.normalized.jsonl
```

## Stable AdaMAST record

This is the recommended public format:

```json
{
  "trace_id": "checkout-017",
  "task": "Complete the checkout workflow",
  "messages": [
    {"role": "user", "content": "Please place the order."},
    {"role": "assistant", "content": "I will check the cart first."},
    {"role": "tool", "name": "get_cart", "content": "{\"items\": []}"}
  ],
  "outcome": {
    "status": "failure",
    "score": 0.0
  },
  "metadata": {
    "system": "checkout-agent",
    "split": "validation"
  }
}
```

Fields:

| Field | Required | Meaning |
|---|---:|---|
| `trace_id` | Recommended | Stable identifier; a deterministic file/index ID is supplied when omitted. |
| `task` | No | Task or user request associated with the trajectory. |
| `messages` | One trajectory field | Ordered role/content messages, optionally including names and tool calls. |
| `raw_trajectory` | One trajectory field | Preformatted trajectory text. May be empty to represent an empty-output failure. |
| `trajectory` | One trajectory field | Alias accepting either text or message objects. |
| `outcome` | No | String or object describing success, failure, score, or verdict. |
| `metadata` | No | User-controlled JSON metadata preserved during normalization. |

Exactly one of `messages`, `raw_trajectory`, `trajectory`, or
`trace.trajectory` must be present.

JSON may contain one record, an array of records, or `{"traces": [...]}`.
JSONL contains one record per non-empty line.

## Compatibility importers

These formats are accepted to reproduce existing work. They are not the stable
AdaMAST schema:

### Message arrays

Records using `messages` or a list-valued `trajectory` are converted to labeled
role blocks. Text, content-part arrays, agent names, and function tool calls are
preserved.

### Event-log JSONL

JSONL records with an `event` key are combined into one trace. BASELINE reads
`run_start`, prompts, responses, completions, agent outputs, and final-answer
events.

### Codex session JSONL

Session files containing `session_meta`, `turn_context`, `response_item`, and
`event_msg` records are converted into one task/tool trajectory.

### tau-bench

Records containing `task_id`, `reward`, and a `traj` message list preserve the
messages, tool calls, task instruction, reward, and trial.

### MAD envelopes

Records containing `mas_name` and `trace.trajectory` preserve the MAS, model,
benchmark, trace identifier, and raw trajectory.

## Validation behavior

Validation fails before generation when:

- a source path does not exist;
- no JSON or JSONL files are found;
- JSON/JSONL syntax is invalid;
- a record is not an object;
- a trace has no recognized trajectory field; or
- a recognized session/event file contains no usable content.

An explicitly supplied empty `raw_trajectory` is valid because missing output
can itself be failure evidence.
