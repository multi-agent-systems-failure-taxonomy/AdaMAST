# Prepare traces

BASELINE and the core trace judge use the same loader. Give AdaMAST one `.json`
or `.jsonl` file, or a directory containing those files, and it normalizes every
accepted record to one stable shape before any model call.

## Canonical AdaMAST record

When you control trace export, use this format:

```json
{
  "problem_id": "trace-17",
  "task": "Optional original task or user request",
  "raw_trajectory": "The complete agent, model, and tool trajectory",
  "metadata": {
    "system": "my-agent",
    "split": "evaluation"
  }
}
```

`problem_id` and `raw_trajectory` are the important fields. `task` may be empty,
and `metadata` may contain any JSON object useful to your experiment. Use a
stable, unique `problem_id` so annotations and artifacts can be traced back to
their source.

## Accepted containers

### JSON

A JSON file may contain one trace object, an array of trace objects, or an
envelope with a `traces` array:

```json
{
  "traces": [
    {
      "problem_id": "trace-1",
      "raw_trajectory": "..."
    }
  ]
}
```

### JSONL

In JSONL, each line is a complete JSON object. Blank lines are ignored.

```json
{"problem_id":"trace-1","task":"...","raw_trajectory":"...","metadata":{}}
{"problem_id":"trace-2","task":"...","raw_trajectory":"...","metadata":{}}
```

### Directory

When the source is a directory, AdaMAST recursively reads every `.json` and
`.jsonl` file in sorted path order. Other file types are ignored.

## Accepted record shapes

| Source shape | Required signal | Normalization behavior |
| --- | --- | --- |
| AdaMAST native | `raw_trajectory` | Preserves the trajectory and metadata |
| String trajectory | `trajectory` or `trace` as a string | Moves the string to `raw_trajectory` |
| Chat messages | `messages`, or `trajectory` as a list | Renders roles, content, names, and tool calls into a readable trajectory |
| MAD / MAST-Data envelope | `mas_name` plus `trace.trajectory` | Preserves MAS, model, benchmark, and source trace identifiers in metadata |
| tau-bench | `traj`, `task_id`, and `reward` | Renders messages and appends the success/failure outcome |
| Codex CLI session | JSONL events such as `session_meta`, `turn_context`, and `response_item` | Combines messages, tool calls, and tool outputs into one trace |
| Generic event log | every JSONL item has `event` | Reads supported run, prompt, response, output, and final-answer events |

For ordinary records, `trace_id`, `problem_id`, or `id` can identify the trace.
`task` or `prompt` can supply the task text. If none of
`raw_trajectory`, `trajectory`, `messages`, or `trace.trajectory` is present,
validation fails instead of guessing.

## Validate before generation

```bash
adamast validate ./my-traces
```

Example report:

```json
{
  "trace_count": 24,
  "files": ["C:/work/my-traces/run.jsonl"],
  "formats": {"messages": 24},
  "empty_trajectories": 0
}
```

Validation is local and makes no model calls. Fix missing or empty trajectories
before starting BASELINE; an empty trajectory cannot provide useful evidence
for drafting or agreement.

## Normalize for inspection

```bash
adamast normalize ./my-traces --output ./traces.normalized.jsonl
```

The normalized file is deterministic UTF-8 JSONL. Inspect it to verify that
roles, tool activity, task text, and outcome signals survived import as
intended. Both `adamast generate` and `adamast judge` perform this normalization
automatically for file-based input.

## Trace quality checklist

- Include the attempted task, not only the final answer.
- Preserve the observations and tool results that support a failure diagnosis.
- Keep successful as well as failed trajectories when they clarify boundaries
  between failure modes.
- Remove secrets and private data before the trace reaches a model provider.
- Keep benchmark labels or oracle outcomes out of the trajectory when they
  would leak the answer to the judge.
- Split unrelated tasks into separate trace records.

Runtime integrations use a related episode-level trace contract; see
[Traces and learning](TRACES_AND_LEARNING.md) after completing the standalone
workflow.
