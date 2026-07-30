# Installation reference

Install AdaMAST for any environment shape: the standard install most readers
need, optional provider extras, and a source checkout for contributors. The
guided path for new users lives on the
[documentation home](index.md#install-adamast); this page records the
dependency variants for repeatable environments.

## ✅ Requirements

- Python 3.10 or newer
- `pip`

!!! note
    Installation has no other requirements. Model-provider credentials are
    needed only later, when you *run* generation or a model-backed judge;
    see [Providers and models](PROVIDERS.md).

## 📦 Standard installation

1. Install from PyPI. This includes the OpenAI adapter and is the
   installation path used throughout the quick start:

    ```bash
    pip install adamast
    ```

2. Verify the core commands:

    ```bash
    adamast --help
    python -m adamast.examples
    adamast validate adamast-examples/traces.jsonl
    ```

!!! note
    Provider-specific credentials and model selection are documented in
    [Providers and models](PROVIDERS.md).

## 🎛️ Make it yours: provider extras

| I need models from… | Install |
|---|---|
| Anthropic | `pip install "adamast[anthropic]"` |
| Google | `pip install "adamast[google]"` |
| AWS Bedrock | `pip install "adamast[bedrock]"` |

Install only the extra you need, unless the environment needs multiple
provider SDKs.

## 🛠️ Source and development installation

For contributors working from a checkout. It is not required to use AdaMAST:

```bash
git clone https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST.git
cd AdaMAST
pip install -e ".[test,docs,anthropic,bedrock]"
pytest
python -m mkdocs build --strict
```

This is the same extras set the release workflow installs; `test` provides
`pytest`, and `docs` provides MkDocs Material.

## ⚡ Installing with uv

`uv` installs AdaMAST into an isolated environment and supplies its own Python,
so no system Python 3.10+ is required:

```bash
uv tool install adamast
```

!!! warning
    Use `uv tool install`, not `uvx`. Host hook commands embed the interpreter
    path at registration time. `uvx` resolves to a content-hashed path inside
    the uv *cache*, which `uv cache clean` or a version bump invalidates —
    every hook then fails silently. `uv tool install` yields a stable venv.

Add `~/.local/bin` to `PATH` afterward, or run `uv tool update-shell`.

## 🔌 Host-specific installation

General package installation stays here and on the documentation home. The
host guides contain only the extra integration steps.

**Claude Code** offers a native plugin that needs no prior package install, or
this package plus `adamast claude install`, which adds project-local scoping and
the full set of install flags. Both install the guidance skill. Compare them in
[Claude Code integration](CLAUDE_CODE.md#choose-an-install-path).

**Codex** also offers a native plugin that manages its own runtime, plus the
package-based `adamast codex install` path for project-local or advanced setup.
See [Codex integration](CODEX.md).
