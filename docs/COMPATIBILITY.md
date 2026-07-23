# Compatibility

Check what AdaMAST needs from your machine, your host, and your credentials
before you install, and what it cannot do yet.

## 🧱 Runtime requirements

- Python 3.10 through 3.14.
- A writable AdaMAST home directory, normally `~/.adamast`.
- Windows and Linux are exercised by CI. Other Python-supported platforms may
  work but are not release-gated yet.

## 🛰️ Codex

- The conversation host must support Codex hooks and allow the installed hooks.
- Project and user hook files are supported.
- Native taxonomy learning uses a subagent in the active Codex task. It does
  not require a standalone `codex` executable or separate login.

!!! tip
    Run `adamast doctor --codex` after installation to verify the hooks and
    native learning configuration.

## 🤖 Claude Code

- The installed Claude Code build must expose the hook event and blocking
  contracts checked by `adamast doctor --claude-code`.
- Native taxonomy learning uses one Agent subtask in the active Claude Code
  session. It does not require a runnable `claude -p` surface or second login.
- The browser selector requires localhost access; use
  `--selector-surface inline` when that surface is unavailable.

## 🔑 Credentials and usage

Native interactive workers do not require `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
or a second external provider account. Codex and Claude Code use the active
task session; each host can still consume its normal included or billed usage.

Provider-backed project installs and direct runtime integrations continue to
support OpenAI-compatible, Anthropic, Gemini, and AWS Bedrock credentials.
Credential values are read from the environment and are never written to the
AdaMAST config.

## 🚧 Current limitations

- Hooks cannot inject a completion notice into an idle conversation. The notice
  is delivered exactly once at the next host lifecycle event.
- Codex uses a compact single-pass Stop checkpoint because a continued desktop
  turn is not guaranteed to invoke Stop again.
- Taxonomy rollback and task-group selection are configuration/runtime controls;
  there is no graphical management surface yet.

!!! warning
    Automatic redaction is enabled by default, but traces can still contain
    sensitive task content. Do not place secrets in prompts or tool output.

## ➡️ Continue with

- [Installation](INSTALLATION.md): the package, extras, and source installs.
- [Choose an interactive host](INTERACTIVE_SETUP.md): pick the Codex or
  Claude Code guide.
