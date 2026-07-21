# Installation reference

The [documentation home](index.md#install-adamast) contains the installation
path new users should follow. This page records the dependency variants for
repeatable environments.

## Requirements

- Python 3.10 or newer
- `pip`
- a writable output directory
- model-provider credentials for generation and model-backed judging

## Standard installation

```bash
pip install adamast
```

This includes the OpenAI adapter and is the installation path used throughout
the quick start.

## Install another provider

```bash
pip install "adamast[anthropic]"
pip install "adamast[google]"
pip install "adamast[bedrock]"
```

Install only one of these commands unless the environment needs multiple
provider SDKs.

## Source and development installation

```bash
git clone https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST.git
cd AdaMAST
pip install -e ".[all,dev,docs]"
pytest
python -m mkdocs build --strict
```

The editable installation is for contributors working from a checkout. It is
not required to use AdaMAST.

## Verify core commands

```bash
adamast --help
adamast validate examples/traces.jsonl
```

Provider-specific credentials and model selection are documented in
[Providers and models](PROVIDERS.md).

## Host-specific installation

General package installation stays here and on the documentation home. The
host guides contain only the extra integration steps:

- [Codex integration](CODEX.md)
- [Claude Code integration](CLAUDE_CODE.md)
