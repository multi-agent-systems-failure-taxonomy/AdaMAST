# Repository tooling

Standalone maintenance scripts. Nothing here ships in the `adamast` package.

| Script | Purpose |
|---|---|
| `build_site.py` | Assemble the deployed site: the [`website/`](../website/) landing page plus the MkDocs documentation build. Run by the `docs` workflow on every docs change. |
| `pypi_readme.py` | Rewrite the root README for the PyPI project page: the release workflow uses `--pypi` to absolutize relative links. |

Run any of them with `python scripts/<name>.py --help` from the repository root.
