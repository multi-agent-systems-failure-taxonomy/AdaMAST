# Architecture

AdaMAST separates the taxonomy engine from the places where agents run. This
keeps Codex, Claude Code, scripts, and custom harnesses on one trace and
activation contract.

## Repository map

| Path | Owns | Does not own |
|---|---|---|
| [`adamast_runtime/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/adamast_runtime) | Sessions, gates, trace persistence, generation/refinement lifecycle, validation, activation, evidence, dashboard data | Host hook formats |
| [`adamast_integration/interactive/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/adamast_integration/interactive) | Conversation selector, browser transport, project/task-group routes, durable native jobs, receipt protocol | Codex or Claude transcript parsing |
| [`adamast_integration/codex/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/adamast_integration/codex) | Codex hook installation, event translation, transcript normalization, compact Stop checkpoint | Taxonomy acceptance |
| [`adamast_integration/claude_code/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/adamast_integration/claude_code) | Claude hook installation, blocking gates, transcript handling, custom hooks | Taxonomy acceptance |
| [`finding/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/finding) | Built-in MAST, taxonomy registry, display metadata, local selector and dashboard views | Learning policy |
| [`judge_types/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/judge_types) | Selection, mapping, coverage, quality, calibration, and reflection judges | Host orchestration |
| [`AdaMAST_as_a_Judge/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/AdaMAST_as_a_Judge) | Judge-focused evaluation checks | Production runtime behavior |
| [`vendor/adamast/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/vendor/adamast) | Vendored research taxonomy-generation pipeline | Interactive hooks |
| [`examples/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/examples) | Runnable demonstrations | Production state |
| [`runs/`](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/runs) | Evaluation artifacts and reproduction notes | Package code |

## Runtime flow

```text
Host event
  -> host adapter resolves project and conversation
  -> interactive selector and route resolve the program
  -> adamast_runtime opens or closes an episode
  -> gate evidence and one canonical trace are persisted
  -> interactive polling checks generation/refinement thresholds
  -> a native host subagent proposes a candidate
  -> adamast_runtime validates and atomically activates it
```

The main agent always owns the user's task. The taxonomy worker receives an
immutable outcome-blind snapshot and cannot edit the taxonomy store. This
separation lets learning continue in parallel without giving a background
worker activation authority.

## Durable project scope

User-level Codex and Claude installs resolve the canonical Git root and store
program state under:

```text
~/.adamast/interactive/
  projects/<project-key>/
    groups/<task-group>/
      program/
```

The project key includes a canonical-path hash, so unrelated repositories with
the same folder name remain isolated. A task group is an explicit subdivision
of one project. Choosing MAST in a project that already has a learned taxonomy
creates a conversation-specific `fresh-*` group without replacing the shared
default.

The first resolved program path is also bound to the host's stable conversation
ID. Subsequent events use that binding before inspecting `cwd`, which keeps a
resumed conversation on the same taxonomy even after a shell or directory
change. On upgrade, AdaMAST locates an existing selected or disabled session and
writes the binding before it can create a new pending selector.

## Stability rules

- Host adapters preserve their documented import and CLI paths.
- Taxonomy activation occurs only in the foreground coordinator.
- One project/task group has at most one active learning job.
- The active taxonomy remains stable while learning runs.
- Invalid or stale candidates leave the current taxonomy unchanged.
- Generated taxonomy IDs are immutable; `display_name` is the user-facing name.

Continue with [Native taxonomy learning](NATIVE_LEARNING.md) for the job state
machine or [Pipeline integration](INTEGRATION.md) for the runtime API.
