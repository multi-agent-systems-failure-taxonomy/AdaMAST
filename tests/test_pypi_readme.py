"""The PyPI/public README transform: note stripping and link absolutizing.

The bundled README becomes the PyPI project page, which neither hides the
private-repository note nor resolves relative links or repository images.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pypi_readme  # noqa: E402


SAMPLE = """# Title

> **Private development repository.** This tree is laid out exactly as the
> public repository should look. See [the workflow](docs/PUBLISHING.md).

Intro line.

<img src="docs/hero.png" width="720" alt="hero">

See [the guide](docs/GETTING_STARTED.md), [runs](runs/), the
[license](LICENSE), ![a chart](docs/chart.png), an
[anchor](#quickstart), and [the site](https://example.com/x).
"""


def test_strip_private_note_removes_only_the_blockquote():
    result = pypi_readme.strip_private_note(SAMPLE)
    assert "Private development repository" not in result
    assert result.startswith("# Title")
    assert "Intro line." in result


def test_absolutize_links_covers_files_dirs_images_and_leaves_absolutes():
    result = pypi_readme.transform(SAMPLE, pypi=True)
    assert (
        'src="https://raw.githubusercontent.com/multi-agent-systems-failure-taxonomy'
        "/AdaMAST/main/docs/hero.png\"" in result
    )
    assert (
        "[the guide](https://github.com/multi-agent-systems-failure-taxonomy"
        "/AdaMAST/blob/main/docs/GETTING_STARTED.md)" in result
    )
    assert (
        "[runs](https://github.com/multi-agent-systems-failure-taxonomy"
        "/AdaMAST/tree/main/runs/)" in result
    )
    assert (
        "[license](https://github.com/multi-agent-systems-failure-taxonomy"
        "/AdaMAST/blob/main/LICENSE)" in result
    )
    assert (
        "![a chart](https://raw.githubusercontent.com/multi-agent-systems-failure-taxonomy"
        "/AdaMAST/main/docs/chart.png)" in result
    )
    assert "[anchor](#quickstart)" in result
    assert "[the site](https://example.com/x)" in result


def test_real_readme_transform_leaves_no_relative_targets():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    result = pypi_readme.transform(text, pypi=True)
    assert "Private development repository" not in result
    for remnant in ("](docs/", "](adamast/", "](runs/", 'src="docs'):
        assert remnant not in result
