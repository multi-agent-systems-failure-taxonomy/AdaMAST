# Repository tooling

Standalone maintenance scripts. Nothing here ships in the `adamast` package.

| Script | Purpose |
|---|---|
| `build_site.py` | Assemble the deployed site: the [`website/`](../website/) landing page plus the MkDocs documentation build. Run by the `docs` workflow on every docs change. |
| `publish_public.py` | Publish this repository to the public AdaMAST repository as a filtered copy: the tracked tree minus every pattern in [`publish.exclude`](../publish.exclude). See [the publishing workflow](../docs/PUBLISHING.md) for the full sequence, including updating `adamast_public.source.json` afterwards. |
| `pypi_readme.py` | Rewrite the root README for an audience outside this repository: the release workflow uses `--pypi` to strip the private-repository note and absolutize relative links for the PyPI project page, and `publish_public.py` uses `--strip-note-only` on the filtered public copy. |

Run any of them with `python scripts/<name>.py --help` from the repository root.
