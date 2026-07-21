"""Build the AdaMAST Pages tree with a landing page and nested docs."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "site"
LANDING_SOURCE = ROOT / "website"


def build_site() -> None:
    """Build MkDocs under ``site/docs`` and copy the landing page to ``site``."""

    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)

    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict"],
        cwd=ROOT,
        check=True,
    )
    shutil.copytree(LANDING_SOURCE, OUTPUT, dirs_exist_ok=True)


if __name__ == "__main__":
    build_site()
