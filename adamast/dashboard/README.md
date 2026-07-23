# adamast/dashboard/

Local web surfaces: the persistent taxonomy dashboard, the shared Codex /
Claude Code checkpoint monitor, the taxonomy viewer, and the blocking
selection web view. Everything serves on localhost only.

## Programs

| File | Purpose |
|---|---|
| [`server.py`](server.py) | `adamast dashboard`: the persistent dashboard, the project/conversation checkpoint monitor, and the `/api/*` routes |
| [`status.py`](status.py) | `adamast status`: active taxonomy, traces, learning state, recent decisions |
| [`viewer.py`](viewer.py) | Read-only taxonomy viewer |
| [`webview.py`](webview.py) | The blocking taxonomy-selection web view used by the interactive picker |

The HTML pages live in [`assets/`](assets/) (`dashboard.html`,
`codex_monitor.html`, `taxonomy_viewer.html`, `webview.html`).
