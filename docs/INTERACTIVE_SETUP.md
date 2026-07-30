# Choose an interactive host

Pick the coding assistant you already use, and this page directs you to the
guide to follow next.

## 🧭 Pick your host

1. Open the guide for your host.
2. Use its native plugin path (recommended, with no Python prerequisite), or
   choose the package CLI path when you need project-local registration or
   advanced installer flags:

| Your host | Follow | That guide covers |
|---|---|---|
| Codex | [Codex integration guide](CODEX.md) | Native plugin and package paths, taxonomy selection, Stop handling, native subagent learning, diagnostics, and uninstall steps |
| Claude Code | [Claude Code integration guide](CLAUDE_CODE.md) | Native plugin and package paths, blocking checkpoints, tool matchers, native Agent learning, diagnostics, and uninstall steps |

!!! tip "Building a script or application instead?"
    For a script or application rather than an interactive coding host, use
    the [single-LLM](SINGLE_LLM.md) or [custom harness](INTEGRATION.md) guide
    instead.

## 🔒 Host-isolated state

!!! note "Each host keeps its own learning state"
    Codex and Claude Code may use the same base `~/.adamast/interactive`
    root, but automatic project routing creates host-owned task groups.
    Conversations on the same host can share an active taxonomy and
    refinement history; conversations on the other host cannot claim that
    program, lineage, trace set, or learning job. Provider-neutral imported
    taxonomies may seed either host. A host-generated or mixed-provenance
    taxonomy is hidden from incompatible selectors.

Continue with [Codex](CODEX.md) or [Claude Code](CLAUDE_CODE.md).
