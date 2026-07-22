# Local web API

AdaMAST exposes a small localhost HTTP API for the runtime dashboard. It is meant
for local monitoring, notebooks, benchmark harnesses, and lightweight external
dashboards.

The API is read-only except for the managed-dashboard shutdown endpoint.

## Start the server

```bash
adamast-dashboard \
  --trace-output ./adamast-program \
  --store-dir ~/.adamast/taxonomies
```

By default the dashboard binds to `127.0.0.1:8765`.

Integrations may start the dashboard automatically when `dashboard` is `true` in
`adamast.json`.

## `GET /api/health`

Returns whether the dashboard process is serving the expected program.

Example:

```json
{
  "program_id": "program-123",
  "monitor_id": "2fe459ef79a545d1",
  "status": "ok"
}
```

Use this for lightweight liveness checks.

## `GET /api/monitor`

Returns the project, conversation, timeline, and checkpoint model used by the
live monitor.

Query parameters:

| Parameter | Meaning |
|---|---|
| `project` | Stable project key to select. |
| `conversation` | Host conversation ID to select. |
| `group` | Optional task-group filter. |
| `window` | `all`, `24h`, `7d`, or `count`. |
| `limit` | Number of recent checkpoints for `window=count` (1–1000). |
| `since` / `until` | Optional Unix timestamp bounds. |

Example shape:

```json
{
  "projects": [
    {
      "project_id": "demo-a1b2c3d4",
      "name": "owner/demo",
      "groups": ["default"],
      "conversation_count": 2,
      "running_count": 1
    }
  ],
  "conversations": [
    {
      "conversation_id": "019f-example",
      "title": "Repair the release workflow",
      "host": "claude_code",
      "host_label": "Claude Code",
      "task_group": "default",
      "status": "running",
      "episode_count": 3,
      "checkpoint_count": 4
    }
  ],
  "selection": {
    "project_id": "demo-a1b2c3d4",
    "conversation_id": "019f-example",
    "task_group": "default",
    "task_group_filter": "all"
  },
  "checkpoints": [
    {
      "seq": 4,
      "checkpoint_id": "adamast-stop-123",
      "episode_sequence": 3,
      "turn_label": "Turn 3",
      "turn_id": "host-turn-or-prompt-id",
      "host": "claude_code",
      "host_label": "Claude Code",
      "gate": "stop",
      "gate_status": "READY_TO_SUBMIT",
      "checkpoint": "Release path verified",
      "fired_codes": [],
      "none_apply": true,
      "evidence": "The package and repository states match.",
      "next_action": "complete"
    }
  ]
}
```

The response also includes the current taxonomy label, failure-mode metadata,
timeline settings, and summary counts. It contains checkpoint reasoning
summaries, not private model chain-of-thought.

## `GET /api/taxonomy`

Returns the current taxonomy view for one program, overlaid with program-local
runtime evidence.

Example shape:

```json
{
  "program_id": "program-123",
  "taxonomy_id": "tax-20260708T000000Z-example",
  "bound_taxonomy_id": "tax-original",
  "is_latest_successor": true,
  "repo": "owner/repo",
  "domain": "display domain",
  "codes": [
    {
      "code_id": "A.1",
      "name": "Skipped verification",
      "description": "The agent declared completion without checking the result.",
      "fire_count": 2,
      "task_firings": [
        {
          "task_id": "session-0118",
          "label": "UID0118 ✗",
          "count": 2
        }
      ],
      "runtime_evidence": [
        {
          "seq": 4,
          "timestamp": 1780000000.0,
          "gate": "final_gate",
          "task_id": "session-0118",
          "task_label": "UID0118 ✗",
          "checkpoint_id": "cp-final",
          "evidence": "The final answer was submitted before any validation.",
          "correlate": "The trajectory matches A.1.",
          "decide": "Run validation before submitting."
        }
      ],
      "fields": [
        {
          "name": "category",
          "value": "Verification"
        }
      ]
    }
  ],
  "clean_checkpoints": [
    {
      "seq": 5,
      "timestamp": 1780000010.0,
      "checkpoint_id": "cp-clean",
      "gate": "final_gate",
      "task_id": "session-0120",
      "task_label": "UID0120 ✓",
      "none_apply": true,
      "considered": ["A.1"],
      "observe": "Validation was performed.",
      "correlate": "No evidence-supported failure remained.",
      "decide": "Proceed."
    }
  ]
}
```

Notes:

- `taxonomy_id` is the latest taxonomy visible to the program.
- `bound_taxonomy_id` is the taxonomy the program originally held before
  lineage resolution.
- `repo` and `domain` are display metadata only.
- `runtime_evidence` is program-local. It overlays the taxonomy view without
  mutating the stored taxonomy record.
- `clean_checkpoints` are accepted reflections where no code fired.
- Evidence text is clipped server-side for dashboard readability.

## `POST /api/shutdown`

This endpoint exists only for dashboards started by AdaMAST integrations through
the managed-dashboard lifecycle. It requires the private `X-AdaMAST-Token` header
written into the program's `.adamast-dashboard.json` state file.

Do not call this endpoint from external dashboards unless you own the managed
dashboard lifecycle.

## Security model

The dashboard is designed for localhost use. Do not expose it publicly without
an external authentication layer.

The API can include task labels, evidence snippets, checkpoint IDs, and reasoning
captured during runtime. Treat the response as run data, not a public asset.
