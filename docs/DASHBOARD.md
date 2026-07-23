# Live monitor

Watch every checkpoint your agent records (the gate label, the evidence, and
which failure modes fired) in a shared, read-only localhost monitor for
checkpoints recorded by Codex and Claude Code.

## 🚀 Open the monitor

No manual launch is needed: either integration opens the monitor
automatically on the project and conversation that started it when
`dashboard` is enabled.

To open it manually, point the command at one program and its interactive
store root:

```bash
adamast dashboard \
  --trace-output ~/.adamast/interactive/projects/PROJECT/groups/default/program \
  --monitor-root ~/.adamast/interactive \
  --store-dir ~/.adamast/taxonomies
```

!!! note
    Without `--monitor-root`, `adamast dashboard` continues to open the
    existing single-program taxonomy dashboard used by standalone and other
    integrations.

## 🧭 Find your conversation

The monitor follows the same hierarchy as interactive AdaMAST state:

1. **Project** selects an isolated Git project or configured project ID.
2. **Conversation** selects a current or recorded Codex or Claude Code
   conversation. The host label remains visible when both tools have
   similarly named conversations.
3. **Timeline** limits the view to all history, the past 24 hours, the past
   seven days, the last N checkpoints, or a custom date range.

Task groups remain an internal storage boundary. The monitor page shows every
group in the selected project; the `group` query parameter on `/api/monitor`
still narrows API responses for direct callers.

The URL carries the selected project and conversation. A monitor opened by
Codex or Claude Code therefore starts on the conversation that spawned it,
while the selectors can move to other recorded work without starting another
server.

!!! note "How conversations are named"
    Conversation selectors use the task title shown by the host, including
    later renames. For Claude Code, AdaMAST reads the session's latest custom
    title. The internal conversation UUID remains in the URL and storage
    records so similarly named tasks do not collide. If host title metadata
    is unavailable, AdaMAST falls back to the first task prompt and then a
    short conversation ID.

## 🔀 Viewing modes

The mode switch transposes the same checkpoint records:

| Mode | What it shows |
|---|---|
| **Failure modes → checkpoints** | Groups checkpoint entries under every failure mode that fired. |
| **Checkpoints → failure modes** | Groups failure-mode entries under each gate. |

Entries remain one line in the ledger. Selecting one opens a detail drawer
with the checkpoint, relevant codes, evidence, next action,
Observe/Correlate/Map/Decide structure, timestamp, gate, host, friendly turn
number, exact host turn or prompt ID, checkpoint ID, and taxonomy ID.

A single Codex turn or Claude prompt may trigger several checkpoints. They
remain separate entries with separate checkpoint IDs and gate names, but
share the same host turn or prompt ID so the monitor can show their
relationship without merging their evidence.

!!! tip "Clean checkpoints"
    Clean `none apply` checkpoints always appear in checkpoint mode. They are
    hidden by default in failure-mode mode and can be included with **Show
    clean in failure view**.

## ⏱️ Live behavior and history

The browser polls the local API every two seconds. Newly recorded
checkpoints appear without reloading. Completed conversations remain discoverable from
their durable host state and program-local evidence files.

Taxonomy generation, refinement, activation, and retention notices are not
checkpoint entries. Taxonomy-refinement lineage across historical checkpoint
versions is intentionally deferred from this first monitor version.

## 🔌 Local Web API

The monitor reads `GET /api/monitor`. The existing program-level
`GET /api/taxonomy` endpoint remains available for integrations that need the
complete current taxonomy overlay. See [WEB_API.md](WEB_API.md).

## 🔒 Local-only behavior

!!! warning
    The monitor binds to localhost by default and can contain task evidence
    and reasoning summaries. Do not expose it publicly without an external
    authentication layer.

For a terminal summary of one program, use:

```bash
adamast status --config adamast.json
```

Continue with [WEB_API.md](WEB_API.md) for the endpoint shapes, or
[Troubleshooting](TROUBLESHOOTING.md) if the monitor does not open.
