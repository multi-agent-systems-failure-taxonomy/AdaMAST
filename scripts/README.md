# Repository tooling

Standalone maintenance scripts. Nothing here ships in the `adamast` package.

| Script | Purpose |
|---|---|
| `build_site.py` | Assemble the deployed site: the [`website/`](../website/) landing page plus the MkDocs documentation build. Run by the `docs` workflow on every docs change. |
| `publish_public.py` | Publish this repository to the public AdaMAST repository as a filtered copy — the tracked tree minus every pattern in [`publish.exclude`](../publish.exclude). See [the publishing workflow](../docs/PUBLISHING.md) for the full sequence, including updating `adamast_public.source.json` afterwards. |

Run either with `python scripts/<name>.py --help` from the repository root.
