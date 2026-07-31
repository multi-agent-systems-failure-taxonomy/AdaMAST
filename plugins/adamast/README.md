# AdaMAST native plugin

Install AdaMAST directly in Claude Code or Codex. The plugin bundles the
guidance skill, lifecycle hooks, and native taxonomy-learning protocol; it
manages its private Python runtime automatically.

## Install

### Claude Code

Run these commands inside Claude Code:

```text
/plugin marketplace add multi-agent-systems-failure-taxonomy/AdaMAST
/plugin install adamast@adamast
```

### Codex

Run these commands in a terminal:

```bash
codex plugin marketplace add multi-agent-systems-failure-taxonomy/AdaMAST
codex plugin add adamast@adamast
```

Then open `/hooks` in Codex and trust the AdaMAST hooks. Hook trust is a Codex
security requirement for every non-managed plugin.

No prior AdaMAST, Python, `pip`, or `uv` installation is required for either
path. Start a new conversation after installing the plugin. The first
`SessionStart` installs the version-pinned runtime synchronously into the
plugin's writable data directory and then continues the same session.

## Platform behavior

| System | Claude Code | Codex |
|---|---|---|
| Windows | Git-Bash launcher with native Python config paths | PowerShell launcher |
| macOS | POSIX launcher | POSIX launcher |
| Linux | POSIX launcher | POSIX launcher |

The launchers:

1. use the host-provided plugin data directory;
2. verify that the managed AdaMAST runtime matches the plugin version;
3. install or repair a private `uv` and AdaMAST runtime when necessary;
4. pass the event to `adamast.hosts.plugin_dispatcher`;
5. let Python create host-native config paths and route to the Claude Code or
   Codex dispatcher.

Bootstrap logs are stored under the host's plugin data directory at
`state/bootstrap.log`. Set `ADAMAST_CONFIG` to use a specific config, or place
`adamast.json` in the project root. Without either, the plugin creates
`~/.claude/adamast.json` or `~/.codex/adamast.json`.

## What ships

| Component | Purpose |
|---|---|
| `.claude-plugin/plugin.json` | Claude Code plugin identity |
| `.codex-plugin/plugin.json` | Codex plugin identity and Codex hook entry |
| `skills/adamast-failure-modes/SKILL.md` | Private checkpoint and final-gate discipline |
| `hooks/claude.json` | Claude Code lifecycle hooks |
| `hooks/hooks.json` | Codex lifecycle hooks, including Windows overrides |
| `agents/adamast-taxonomy-worker.md` | Claude Code taxonomy proposal worker |
| `bin/adamast-hook` | macOS/Linux and Claude Code launcher |
| `bin/adamast-hook.cmd` | Windows Codex entry point and PowerShell environment shim |
| `bin/adamast-hook.ps1` | Windows-native Codex bootstrap |

## No API key required

The default profile uses `adamast_model: "interactive-session"` and the
authenticated host's subagent backend (`claude_subagent` or `codex_subagent`).
Taxonomy generation and refinement therefore use the current host session
rather than a separate provider key.

## Package CLI alternative

The native plugin and package installer are alternative ways to register the
same runtime. Do not enable both for one host, because every lifecycle event
would fire twice.

Use the package path when you need a project-local registration or advanced
installer flags:

```bash
pip install adamast
adamast claude install --user-level
# or
adamast codex install --user-level
```

## Uninstall

```text
/plugin uninstall adamast@adamast
```

```bash
codex plugin remove adamast@adamast
```

Either one stops every hook. The marketplace stays registered so a reinstall
does not need re-adding it; drop it as well with
`/plugin marketplace remove adamast` or `codex plugin marketplace remove
adamast`.

Learned taxonomies, trace folders, and the host's `adamast.json` survive on
purpose, so a reinstall resumes where you stopped. Delete `~/.adamast/` and
that config by hand for a clean slate.
