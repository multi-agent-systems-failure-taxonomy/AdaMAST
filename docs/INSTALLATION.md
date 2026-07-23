# Installation reference

Install AdaMAST for any environment shape: the standard install most readers
need, optional provider extras, and a source checkout for contributors. The
guided path for new users lives on the
[documentation home](index.md#install-adamast); this page records the
dependency variants for repeatable environments.

## ✅ Requirements

- Python 3.10 or newer
- `pip`
- a writable output directory
- model-provider credentials for generation and model-backed judging

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

## 🔌 Host-specific installation

General package installation stays here and on the documentation home. The
host guides contain only the extra integration steps.

Continue with [Codex integration](CODEX.md) or
[Claude Code integration](CLAUDE_CODE.md).
