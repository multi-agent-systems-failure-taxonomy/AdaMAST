# Installation reference

The [documentation home](index.md#install-adamast) contains the installation
path new users should follow. This page records the dependency variants for
repeatable environments.

## Requirements

- Python 3.10 or newer
- `pip` with editable-install support
- a writable output directory
- model-provider credentials for generation and model-backed judging

## Install from the public repository

```bash
git clone https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST.git
cd AdaMAST
python -m pip install -e ".[all]"
```

## Install one provider only

```bash
python -m pip install -e ".[openai]"
python -m pip install -e ".[anthropic]"
python -m pip install -e ".[google]"
python -m pip install -e ".[bedrock]"
```

Install only one of these commands unless the environment needs multiple
provider SDKs.

## Development install

```bash
python -m pip install -e ".[all,dev,docs]"
pytest
python -m mkdocs build --strict
```

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
