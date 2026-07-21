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

## Shared state

When both hosts use the same project root, task group, and `trace_output`, they
can share the active taxonomy and refinement history. Different projects remain
isolated.

For a script or application rather than an interactive coding host, use the
[single-LLM](SINGLE_LLM.md) or [custom harness](INTEGRATION.md) guide instead.
