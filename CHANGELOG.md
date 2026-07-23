# Changelog

All notable user-facing changes are documented here.

## Unreleased

### Changed

- Single-package layout (the 0.2.0 layout): all code now lives in one
  `adamast` package — `adamast.core` (data model, taxonomy store, session
  lifecycle), `adamast.protocol` (the one compact-checkpoint
  implementation, previously duplicated by the Claude Code and Codex
  transports, plus the pre-submission gate), `adamast.judges`,
  `adamast.llm`, `adamast.learning` (generation/refinement, learning jobs,
  the vendored pipeline), `adamast.hosts`, `adamast.dashboard`, and
  `adamast.cli`.
- Installed hook commands, spawned learning workers, and recorded
  `worker_module` values now use the canonical `adamast.*` module paths.

### Removed

- **Breaking:** the historical top-level packages (`adamast_runtime`,
  `adamast_integration`, `finding`, `judge_types`, `vendor`) are gone;
  every module now lives only in the `adamast` package. Imports and
  `python -m` commands using the old paths stop working. Installations
  made from `adamast==0.1.0` (or hooks it registered) must uninstall with
  their own version's uninstaller, upgrade, and reinstall — pip removes
  the deleted packages from site-packages automatically on upgrade. The
  config schema moved from `adamast_runtime/assets/` to
  `adamast/core/assets/`.
- The public-mirror staging fork (`adamast_public/`) is gone. Its
  public-only surfaces — the provider-neutral JUDGES contract
  (`adamast.judges.contract`), providers (`adamast.llm.providers`), the
  draft/agreement generation engines (`adamast.learning.pipeline`), the
  public API (`adamast.learning.api`), trace-format normalization
  (`adamast.core.trace_formats`), and the taxonomy viewer
  (`adamast.dashboard.viewer`) — moved into the package, and
  `from adamast import generate_taxonomy, judge_trace` works as the public
  docs describe. `python -m adamast` now runs the umbrella CLI. Publishing
  the public repository is a filtered copy of this one.

### Added

- A single `adamast` umbrella command (`adamast doctor`, `adamast claude
  install`, `adamast dashboard`, ...) routing to the existing CLIs. All
  `adamast-*` scripts keep working; the umbrella matches the public
  package's interface so the command surface survives the layout
  migration.
- `adamast doctor` now ends its text report with an actionable "Next
  step" line instead of leaving a warning as the last word.
- `adamast-claude-install --help` and `adamast-codex-install --help` are
  grouped (getting started / learning behavior / advanced tuning / hook
  selection) with a quickstart epilog, instead of a flat wall of flags.

### Fixed

- Taxonomy code ids are now stable identities: `Taxonomy.from_flat`
  preserves registered ids verbatim (sparse dotted, `MAST-12`-shaped, and
  bare numeric alike) instead of renumbering everything to dense
  `{category}.{n}`, and `renumber()` only assigns ids to codes that lack
  one. New codes mint above a per-category high-water mark persisted as
  `id_high_water` through refinement and registration, so a retired id is
  never reused and recorded evidence can never be silently reattributed to
  a different failure mode by a refinement pass. Refiner MERGE mints a
  fresh id for the merged concept; retire leaves an honest gap.
- Compact checkpoints (`adamast-claude-checkpoint`, the Codex compact final
  block) now record cited taxonomy codes for every registered id shape.
  Citation matching previously required hyphen-digit ids and, on the Claude
  Code path, read a key the registered state does not use, so citations of
  the dotted (`A.1`) and numeric (`1`) ids that registered taxonomies emit
  were rejected and only `none apply` checkpoints could be recorded. Both
  transports now share the reflection parser's exact, boundary-guarded
  matcher, and assignments follow taxonomy order deterministically.
- Project URLs point at the AdaMAST repository instead of the retired ATLAS
  repository.

- Codex and Claude Code automatic project programs are now host-owned. Runtime
  selectors, active-taxonomy checks, provenance stamping, and native job claims
  reject cross-host or mixed-provenance state.
- Codex and Claude Code browser selectors now pause the first prompt until a
  taxonomy is chosen, then release that original prompt immediately instead of
  ending the turn and requiring a second submission.
- The managed dashboard's readiness budget is 15 seconds by default (was 5)
  and can be overridden with `ADAMAST_DASHBOARD_TIMEOUT`; the previous fixed
  budget made dashboard startup flake on slow hosts and CI runners.

## 0.1.0 - 2026-07-16

AdaMAST is the new name of this project (previously ATLAS, distributed as
`atlas-skill`). Versioning restarts at 0.1.0 under the new `adamast`
distribution name; entries below 0.1.0 describe the `atlas-skill` lineage.

### Changed

- Package renamed `atlas-skill` → `adamast`; every `atlas-*` console command
  is now `adamast-*` (`adamast-claude-install`, `adamast-doctor`, …).
- Python packages renamed: `atlas_integration` → `adamast_integration` and
  `atlas_runtime` → `adamast_runtime`.
- User-level config and state paths renamed: `~/.claude/atlas-skill.json` →
  `~/.claude/adamast.json`, `~/.codex/atlas-skill.json` →
  `~/.codex/adamast.json`, data home `~/.atlas-skill` → `~/.adamast`, and
  the project-local config default `atlas.json` → `adamast.json`.
- Environment variables renamed to `ADAMAST_*` (`ADAMAST_HOME`,
  `ADAMAST_STORE_DIR`, `ADAMAST_TRACE_ROOT`, `ADAMAST_DISABLE_DASHBOARD`,
  `ADAMAST_JUDGE_CAP`); the config/manifest key `atlas_model` is now
  `adamast_model`; the learning receipt tag is now
  `<ADAMAST_TAXONOMY_RECEIPT>`.
- The vendored research generation pipeline is now `vendor/adamast` (env
  vars `ADAMAST_MODEL`, `ADAMAST_TIMEOUT`, `ADAMAST_MAX_WORKERS`,
  `ADAMAST_MAX_CODES`), and the paper is referenced as the AdaMAST paper
  (`docs/adamast_paper.pdf`). The built-in MAST taxonomy is unchanged.

### Added

- The `release` workflow publishes to PyPI through Trusted Publishing after
  the GitHub release job succeeds.

### Migration

- Uninstall the old package before installing the new one — both ship the
  shared `finding`, `judge_types`, and `vendor` modules and would collide:
  run `atlas-claude-uninstall --user-level` / `atlas-codex-uninstall
  --user-level`, `pip uninstall atlas-skill`, then `pip install adamast` and
  re-run `adamast-claude-install --user-level` /
  `adamast-codex-install --user-level`.
- To keep learned state, move `~/.atlas-skill` to `~/.adamast`, rename
  `.atlas-*` marker files and directories inside it to `.adamast-*`, and
  rename the `atlas_model` key in program manifests to `adamast_model`.

## 1.1.0b7 - 2026-07-15

### Changed

- Codex hook registrations now use distinct transient status messages for
  taxonomy restore, state checks, trace saving, learning reconciliation, and
  polling.
- The managed Codex skill now requires one compact checkpoint after an actual
  failed tool call, using evidence already visible to the agent.
- Codex learning lifecycle notices remain queued through terminal `Stop` and
  `SubagentStop` events and are consumed only when `SessionStart` or
  `UserPromptSubmit` can render them through the active model.

### Fixed

- Codex `PostToolUse` no longer emits an unsupported `additionalContext`
  payload or guesses failures from successful source and log text.
- Learning lifecycle notices are no longer consumed by a `PostToolUse` event
  that cannot deliver them to the active Codex model.

## 1.1.0b6 - 2026-07-15

### Fixed

- Codex `SubagentStop` hooks reconcile completed candidate or support-review
  receipts but no longer claim the next native-learning phase. Claims now wait
  for `SessionStart` or `UserPromptSubmit`, which can deliver the launch
  directive to the active model instead of stranding an invisible support job.

## 1.1.0b5 - 2026-07-15

### Changed

- Codex installs `SessionStart` for context compaction as well as startup and
  resume, giving long-running desktop tasks a supported developer-context
  boundary for native taxonomy dispatch when `UserPromptSubmit` is absent.
- Native learning now reports the independent support-review stage explicitly,
  and the manifest's diagnostic job summary stays aligned with the durable job
  file while jobs are queued, claimed, or waiting for activation.
- Native subagent receipt examples now contain real nested JSON objects instead
  of quoted placeholders. One legacy stringified object layer is decoded before
  the same strict candidate or support-review validation runs.

### Fixed

- Visible prose in refinement prompts and generated taxonomy summaries now uses
  a typographic em dash instead of a double hyphen. Command options, CSS custom
  properties, class names, badges, and Markdown syntax remain unchanged.

## 1.1.0b4 - 2026-07-15

### Fixed

- Codex no longer opens the taxonomy browser from a new `SessionStart` event.
  The picker opens on the first real `UserPromptSubmit`, so background host
  tasks and spawned agent sessions cannot create unsolicited browser windows.
- Native learning polling and job claims now require an active taxonomy
  selection when the conversation selector is enabled. Internal sessions no
  longer receive recursive taxonomy-generation directives.
- Resuming a selected Codex conversation remains silent and preserves its
  existing taxonomy; resuming an unselected pending conversation waits for the
  next user prompt instead of reopening the browser immediately.
- Detached taxonomy pickers now tolerate slow Windows interpreter startup and
  terminate their worker if readiness still fails, instead of leaving an
  orphaned localhost process.

## 1.1.0b3 - 2026-07-15

### Added

- Codex-native taxonomy learning now uses a subagent in the active task with a
  durable claim/receipt protocol. It no longer requires a standalone Codex CLI
  login or an external model API key.
- Every Codex lifecycle hook polls generation and refinement progress,
  idempotently repairing a missed threshold trigger on the next event.
- Codex conversations can select MAST in a project that already has a learned
  taxonomy. The selection creates a durable isolated `fresh-*` task group and
  preserves the project's shared default taxonomy.
- Codex conversations open a session-bound localhost taxonomy library directly
  from `SessionStart`. The browser applies and persists the choice before
  reporting success, without depending on a later `UserPromptSubmit` event.
- The taxonomy library now uses the AdaMAST runtime visual language, provides a
  searchable taxonomy rail and full code inspection, and treats generated
  evidence as secondary expandable provenance.
- Claude Code taxonomy learning now uses native Agent subtasks in the active
  session with the same durable claim/receipt lifecycle as Codex. It no longer
  requires a standalone `claude -p` login or external model API key.
- Claude Code now supports the session-bound browser library, direct taxonomy
  activation, durable fresh-MAST conversation routes, and missed-threshold
  polling on every successful lifecycle hook.
- The documentation site now leads with host installation, native-learning
  behavior, architecture ownership, and a complete local dashboard example.

### Changed

- Codex taxonomy workers receive only a frozen prompt and output schema, then
  return a bounded receipt through `SubagentStop`. The foreground hook
  coordinator remains the sole validator and activation owner.
- `codex.worker_model` and `codex.codex_cli_path` remain readable compatibility
  fields but are not used by native in-task learning.
- `claude_code.worker_model` and `claude_code.claude_cli_path` remain readable
  compatibility fields but are not used by native in-session learning.
- Taxonomy selectors and the browser catalog prefer a human-facing
  `display_name` while keeping generated taxonomy IDs as immutable internal
  keys. Older records fall back to their domain, and new native candidates may
  provide a concise display name.
- Host-neutral browser transport, fresh-session routing, threshold polling,
  and receipt validation now live in `adamast_integration/interactive`; Codex and
  Claude Code retain stable facade modules for compatibility.

### Fixed

- Final-gate status parsing now accepts a bounded vocabulary and uses a
  runtime-owned repair counter, preventing negated prose or model-supplied
  attempt counts from bypassing the gate.
- Native taxonomy replacement codes now require exact frozen-trace quotes;
  activation verifies every span and records a per-code validation result.
- Active sessions now carry heartbeat leases, and activation/status paths
  conservatively reconcile abandoned sessions after a legacy grace lease.
- Malformed or unreadable trace files now abort learning snapshots with the
  affected path instead of silently changing the evidence set.
- Codex compact checkpoints recognize `no further action required` as complete.
- Codex and Claude uninstallers now remove exact managed dispatcher commands
  while preserving unrelated hooks with AdaMAST-like names or config paths.
- Repository licensing, vendored-pipeline provenance, result-artifact claims,
  package maps, linting, and a 78% coverage floor now match the shipped files.

- Codex user-level hooks now ignore host-maintenance conversations rooted in
  `~/.codex/memories`, preventing internal memory work from opening a taxonomy
  browser alongside an unrelated user task.
- Resumed Codex and Claude Code conversations recover an exact legacy inline
  taxonomy reply from their transcript before launching the browser. This
  repairs selections missed when `UserPromptSubmit` was not emitted.
- Codex and Claude Code context now distinguishes the taxonomy originally
  selected as a lineage seed from the generated or refined taxonomy currently
  active. Checkpoints are explicitly directed to the active taxonomy's codes
  instead of continuing to present MAST as pinned after activation.
- Resumed Codex and Claude Code conversations now retain their original AdaMAST
  program scope even when the host reports a different current working
  directory. Existing selected or disabled session state is migrated into the
  new durable conversation-scope binding before a selector can reopen.
- The browser selector confirmation now names the active host instead of
  always telling Claude Code users to return to Codex.
- Codex Desktop sessions that omit `UserPromptSubmit` no longer remain stuck in
  `AdaMAST is waiting for taxonomy selection` after a browser choice.
- The Codex selector now displays MAST as a numbered choice even when the
  project already has a learned taxonomy; its reply instructions no longer
  advertise a hidden option.
- Claude taxonomy receipts bypass the ordinary blocking `SubagentStop`
  reflection, preventing the learning Agent from recursively gating itself;
  all other Claude subagents retain the existing checkpoint behavior.

- The user-level interactive placeholder model (`interactive-session`)
  adopts whatever AdaMAST model a program already records instead of raising
  a conflict. Program state written by an earlier release (which recorded
  the old default model) no longer fails every hook event in previously
  used projects.

## 1.1.0b2 - 2026-07-14

### Fixed

- Claude Code hooks are registered as `python -m
  adamast_integration.claude_code.dispatcher` instead of an absolute dispatcher
  file path, so switching between wheel and editable installs (or relocating
  the package) no longer breaks every hook event.
- Shared state files (program manifest, session state, worker heartbeats,
  traces, learning jobs, evidence, dashboard state) retry atomic replaces and
  reads on Windows sharing violations instead of failing hooks and background
  learning jobs with transient `PermissionError`.
- Read-only manifest lock cycles (activation polls, cadence checks) no longer
  rewrite the manifest file on every exit.
- Hook dispatchers pin stdin/stdout/stderr to UTF-8, fixing mojibake in gate
  and selector text on Windows hosts.
- Native learning workers scrub the spawning session's transport variables
  (`ANTHROPIC_BASE_URL`, host OAuth plumbing, `OPENAI_BASE_URL`) so the
  detached CLI authenticates with its own persisted login instead of failing
  with 401s against a session-scoped gateway. Deliberate user API keys are
  preserved.

## 1.1.0b1 - 2026-07-14

### Added

- User-level, zero-config Codex and Claude Code installers.
- One completed assistant episode per trace for interactive conversations.
- Automatic Git-project and task-group scoping shared across conversations.
- In-chat taxonomy selection with MAST, compatible stored taxonomies, and an
  AdaMAST-off choice.
- Detached native taxonomy generation and refinement workers that reuse the
  signed-in Codex or Claude Code CLI instead of requiring a separate API key.
- Visible, exactly-once generation and refinement trigger/completion notices.
- Durable, idempotent learning jobs with frozen evidence snapshots, stale-job
  recovery, validation before activation, and taxonomy lineage.
- Interactive installation and native-worker diagnostics in `adamast-doctor`.

### Changed

- Codex skill guidance now installs to the documented user skill directory,
  `~/.agents/skills`.
- Codex and Claude Code use a shared interactive learning-state contract while
  retaining host-specific worker launchers.
- Bedrock taxonomy calls honor the configured AdaMAST timeout and adaptive retry
  policy.

### Compatibility

- Existing `codex_learning` manifest state migrates to `interactive_learning`
  on first use.
- Project-local, provider-backed installs keep their existing required config
  and defaults.

## 1.0.0

- Initial packaged AdaMAST runtime with MAST fallback, trace persistence,
  generation/refinement, dashboard, Claude Code, Codex, single-LLM, and
  harness-neutral integrations.
