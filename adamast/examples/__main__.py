"""Copy the bundled example files into ./adamast-examples/.

Run ``python -m adamast.examples``; the quickstart then uses the copied
files (``adamast validate adamast-examples/traces.jsonl``). Works the same
from a pip install and from a source checkout.
"""

from __future__ import annotations

import shutil
import sys
from importlib.resources import files
from pathlib import Path

_DATA = ("traces.jsonl", "taxonomy.sample.json")
_SCRIPTS = ("dashboard_demo.py", "judge_usage.py", "manual_vendored_generation.py")


def main(argv: list[str] | None = None) -> int:
    target = Path((argv or sys.argv[1:] or ["adamast-examples"])[0])
    target.mkdir(parents=True, exist_ok=True)
    package = files("adamast.examples")
    for name in (*_DATA, *_SCRIPTS):
        with (package / name).open("rb") as source, (target / name).open("wb") as sink:
            shutil.copyfileobj(source, sink)
    print(f"Copied {len(_DATA) + len(_SCRIPTS)} example files to {target.resolve()}")
    for name in _DATA:
        print(f"  {target / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
