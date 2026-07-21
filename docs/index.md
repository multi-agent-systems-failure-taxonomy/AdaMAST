# AdaMAST documentation

AdaMAST adds a failure-mode taxonomy layer to agent workflows. It records
evidence from completed traces, supports taxonomy generation and refinement,
and applies the active taxonomy at evaluation or runtime boundaries.

This documentation covers installation, the taxonomy lifecycle, supported
integrations, and the operational interfaces used to inspect and maintain an
AdaMAST deployment.

## Quickstart

- **[Installation](INSTALLATION.md)** — install AdaMAST and the optional
  provider dependencies needed by your workflow.
- **[AdaMAST 5-minute start](GETTING_STARTED.md)** — configure one project and
  run the core session lifecycle.
- **[Interactive setup](INTERACTIVE_SETUP.md)** — install AdaMAST for ordinary
  Codex or Claude Code conversations.
- **[Example run](EXAMPLE_RUN.md)** — follow a checkpoint, diagnosis, trace,
  and taxonomy update from beginning to end.

## Understand the taxonomy lifecycle

- **[Concepts](CONCEPTS.md)** defines the vocabulary used throughout the
  documentation.
- **[Traces and learning](TRACES_AND_LEARNING.md)** explains what AdaMAST
  records and when generation or refinement runs.
- **[Taxonomies](TAXONOMIES.md)** documents taxonomy records, identifiers,
  inheritance, and activation.
- **[Native taxonomy learning](NATIVE_LEARNING.md)** describes generation and
  refinement inside Codex and Claude Code.
- **[Architecture](ARCHITECTURE.md)** shows which component owns each part of
  the system.

## Choose an integration

| Use case | Guide |
|---|---|
| Use AdaMAST in Codex | [Codex integration](CODEX.md) |
| Use AdaMAST in Claude Code | [Claude Code integration](CLAUDE_CODE.md) |
| Wrap a script, notebook, benchmark, or model call | [Single-LLM integration](SINGLE_LLM.md) |
| Add AdaMAST to a custom agent harness | [Runtime API and custom harnesses](API_OR_RUNTIME.md) |
| Own the complete pipeline lifecycle | [Pipeline integration](INTEGRATION.md) |

## Operate and extend AdaMAST

- **[Configuration reference](CONFIGURATION.md)** lists every supported
  `adamast.json` field, default, and precedence rule.
- **[Dashboard](DASHBOARD.md)** explains the local taxonomy and trace viewer.
- **[Local web API](WEB_API.md)** documents the read-only monitoring endpoints.
- **[Troubleshooting](TROUBLESHOOTING.md)** starts with health checks and common
  recovery steps.
- **[Compatibility](COMPATIBILITY.md)** records supported hosts and current
  limits.
- **[Customization](CUSTOMIZATION.md)** points to the prompts, hooks, judges,
  schemas, and model profiles intended for extension.

## Research

The AdaMAST method and evaluation are described in
[Fantastic Adaptive Taxonomies and How to Use Them](https://arxiv.org/abs/2607.16387).
