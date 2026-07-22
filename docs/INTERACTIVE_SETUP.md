# Choose an interactive host

Install the AdaMAST package from the
[documentation home](index.md#install-adamast) before enabling a host
integration. The host pages contain the additional commands and behavior that
apply only to that environment.

## Codex

Use the [Codex integration guide](CODEX.md) for user-level or project-local
hooks, taxonomy selection, Stop handling, native subagent learning, diagnostics,
and uninstall steps.

## Claude Code

Use the [Claude Code integration guide](CLAUDE_CODE.md) for user-level or
project-local hooks, blocking gates, tool matchers, native Agent learning,
diagnostics, and uninstall steps.

## Host-isolated state

Codex and Claude Code may use the same base `~/.adamast/interactive` root, but
automatic project routing creates host-owned task groups. Conversations on the
same host can share an active taxonomy and refinement history; conversations on
the other host cannot claim that program, lineage, trace set, or learning job.
Provider-neutral imported taxonomies may seed either host. A host-generated or
mixed-provenance taxonomy is hidden from incompatible selectors.

For a script or application rather than an interactive coding host, use the
[single-LLM](SINGLE_LLM.md) or [custom harness](INTEGRATION.md) guide instead.
