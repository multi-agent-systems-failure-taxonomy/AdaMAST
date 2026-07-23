# Releasing AdaMAST

Cut a release: verify the tree, tag it, and let the workflow publish to GitHub
and to PyPI as `adamast` through Trusted Publishing. The public docs advertise
`pip install adamast`.

## 📦 Layout migration: 0.1.0 -> 0.2.0

PyPI `adamast==0.1.0` shipped the multi-package layout (`adamast_runtime`,
`adamast_integration`, `finding`, `judge_types`, `vendor`, `adamast-*`
console scripts). **0.2.0 is this repository's layout**: one real
`adamast` package (`adamast.core`, `adamast.protocol`, `adamast.judges`,
`adamast.llm`, `adamast.learning`, `adamast.hosts`, `adamast.dashboard`,
`adamast.cli`). The historical top-level packages are **removed** — imports
and `python -m` commands under the old paths stop working in 0.2.0. There is
no separate public build anymore; the public repository is a filtered copy of
this one (see [docs/PUBLISHING.md](docs/PUBLISHING.md)).

**Note:** An installation made from 0.1.0 must uninstall with its own
version's uninstaller before upgrading (its hooks reference module paths that
no longer exist), then reinstall after the upgrade. pip removes the deleted
packages from site-packages automatically on upgrade; the `adamast-*` console
scripts keep their names and the umbrella `adamast` command is the primary
interface. The CHANGELOG carries the breaking-change entry.

## 🧪 Prepare

1. Update `pyproject.toml` and move the matching `CHANGELOG.md` section out of
   `Unreleased`.
2. Run the verification bundle:

   ```bash
   python -m compileall -q adamast
   python -m pytest tests -q --cov=adamast --cov-report=term --cov-fail-under=78
   python scripts/build_site.py
   python -m build
   python -m twine check dist/*
   git diff --check
   ```

3. Merge the reviewed release commit to `main` and confirm the `ci` and `docs`
   workflows pass.

## 🏷️ Publish a GitHub release

Create and push a tag that exactly matches the package version:

```bash
git tag -a v0.2.0 -m "AdaMAST 0.2.0"
git push origin v0.2.0
```

The `release` workflow then does the rest:

1. Reruns tests and the strict documentation build.
2. Builds the wheel and source distribution and validates both with Twine.
3. Attaches them to a generated GitHub release.
4. Publishes to PyPI through the `pypi-publish` job (Trusted Publishing, no
   stored token).

**Note:** Alpha, beta, and release-candidate tags are marked as prereleases.

## 🔐 PyPI Trusted Publishing (one-time setup)

**Note:** Registration is complete: the active publisher targets repository
`AdaMAST-private` with workflow `release.yml` and environment `pypi`
(added 2026-07-23; v0.1.0 originally published through the historical
`ATLAS` entry). The steps remain here for reference and for new projects.

The `pypi-publish` job authenticates with an OIDC id-token; PyPI must be told
to trust this repository first:

1. On pypi.org → Account → Publishing, add a **publisher** for the project
   name `adamast` with owner `multi-agent-systems-failure-taxonomy`,
   repository `AdaMAST-private`, workflow `release.yml`, and environment
   `pypi`.
2. Optionally protect the `pypi` environment in the GitHub repository
   settings (it is created automatically on the first run otherwise).
3. Push a version tag and verify `python -m pip install adamast` in a clean
   environment.
4. If releases ever move to another repository, update the trusted
   publisher's repository field on pypi.org to match.
