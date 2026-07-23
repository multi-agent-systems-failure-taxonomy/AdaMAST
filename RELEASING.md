# Releasing AdaMAST

Releases publish to GitHub and to PyPI as `adamast` through Trusted
Publishing. The pending publisher was claimed by the v0.1.0 release and the
public docs advertise `pip install adamast`.

## Layout migration: 0.1.0 -> 0.2.0

PyPI `adamast==0.1.0` shipped the multi-package layout (`adamast_runtime`,
`adamast_integration`, `finding`, `judge_types`, `vendor`, `adamast-*`
console scripts). **0.2.0 is this repository's layout**: one real
`adamast` package (`adamast.core`, `adamast.protocol`, `adamast.judges`,
`adamast.llm`, `adamast.learning`, `adamast.hosts`, `adamast.dashboard`,
`adamast.cli`) with the historical top-level packages kept as thin
compatibility shims. There is no separate public build anymore; the public
repository is a filtered copy of this one (see
[docs/PUBLISHING.md](docs/PUBLISHING.md)). Because the shims re-export the
canonical modules, every 0.1.0 import path and every `adamast-*` console
script keeps working in 0.2.0; the umbrella `adamast` command is the
primary interface. State the layout change in the changelog when cutting
0.2.0 and note that the old import paths are compatibility surfaces slated
for removal in a later major release.

## Prepare

1. Update `pyproject.toml` and move the matching `CHANGELOG.md` section out of
   `Unreleased`.
2. Run the verification bundle:

   ```bash
   python -m compileall -q adamast
   python -m pytest -q
   python scripts/build_site.py
   python -m build
   python -m twine check dist/*
   git diff --check
   ```

3. Merge the reviewed release commit to `main` and confirm the `ci` and `docs`
   workflows pass.

## Publish a GitHub release

Create and push a tag that exactly matches the package version:

```bash
git tag -a v0.1.0 -m "AdaMAST 0.1.0"
git push origin v0.1.0
```

The `release` workflow reruns tests and the strict documentation build, builds
the wheel and source distribution, validates both with Twine, attaches them to
a generated GitHub release, and then publishes to PyPI through the
`pypi-publish` job (Trusted Publishing, no stored token). Alpha, beta, and
release-candidate tags are marked as prereleases.

## PyPI Trusted Publishing (one-time setup)

The `pypi-publish` job authenticates with an OIDC id-token; PyPI must be told
to trust this repository first:

1. On pypi.org → Account → Publishing, add a **pending publisher** for the
   project name `adamast` with owner `multi-agent-systems-failure-taxonomy`,
   repository `ATLAS`, workflow `release.yml`, and environment `pypi`.
2. Optionally protect the `pypi` environment in the GitHub repository
   settings (it is created automatically on the first run otherwise).
3. Push a version tag; the first successful publish claims the `adamast`
   name. Verify `python -m pip install adamast` in a clean environment.
4. Only then change public docs to `python -m pip install adamast`.
5. If the repository is later migrated (e.g. to the `AdaMAST` repo), update
   the trusted publisher's repository field on pypi.org to match.
