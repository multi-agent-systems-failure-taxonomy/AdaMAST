# Contributing

Set up a development install, verify your change the same way CI does, and
find the right place in the tree for it.

## 🛠️ Development setup

Python 3.10 or newer is required.

```bash
git clone https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST-private.git
cd AdaMAST-private
python -m pip install -e ".[test]"
```

## ✅ Verify before submitting

Run all four checks:

```bash
python -m compileall adamast
python -m ruff check .
python -m pytest -q --cov=adamast --cov-report=term --cov-fail-under=78
git diff --check
```

## 🗺️ Where things live

All code lives in the single `adamast` package (see the repository map in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)). Useful maps:

| Area | Map |
|---|---|
| Shared selectors, routes, jobs, and receipts | [adamast/hosts/interactive/README.md](adamast/hosts/interactive/README.md) |
| Claude Code adapter | [adamast/hosts/claude_code/README.md](adamast/hosts/claude_code/README.md) |
| Codex adapter | [adamast/hosts/codex/README.md](adamast/hosts/codex/README.md) |
| Reproducible experiment artifacts | [runs/README.md](runs/README.md) |
| Test suite map | [tests/README.md](tests/README.md) |

User-facing behavior (prompts, hooks, judge specs) lives in Markdown/JSON
assets where possible; start with
[docs/CUSTOMIZATION.md](docs/CUSTOMIZATION.md) before editing Python.

**Note:** Before adding behavior to a host adapter, check
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Event and transcript translation
belong in the host folder; selector, routing, and browser-transport behavior
shared by Codex and Claude Code belongs in `adamast/hosts/interactive/`, and
the shared learning-job engine in `adamast/learning/`.

## 📚 Documentation

Markdown pages in [docs/](docs/) are the documentation source of truth.
[website/](website/) contains the temporary project-root landing page.
[scripts/build_site.py](scripts/build_site.py) combines them into one Pages
artifact: the landing page at `/` and MkDocs under `/docs/`. The private
workflow validates and stores that artifact; the public repository owns
deployment.

Preview locally:

```bash
python -m pip install -e ".[docs]"
python scripts/build_site.py
python -m http.server 8000 --directory site
```

**Note:** The canonical config reference is
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) — other pages should show
minimal configs and link there rather than duplicating field tables.

Release versioning, artifact checks, tags, and the future PyPI trusted-publisher
path are documented in [RELEASING.md](RELEASING.md).
