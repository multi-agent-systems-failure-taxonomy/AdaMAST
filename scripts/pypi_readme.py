#!/usr/bin/env python
"""Prepare README.md for an audience outside this repository.

Two consumers:

* The release workflow runs ``--pypi --in-place`` right before
  ``python -m build``: the PyPI project page is the README bundled into the
  artifact, and PyPI neither hides the private-repository note nor resolves
  relative links or repository-hosted images. This mode strips the note and
  rewrites every relative link/image to an absolute public-repository URL.
* ``publish_public.py`` runs ``--strip-note-only`` on the filtered copy:
  GitHub resolves relative links fine, so the public repository README only
  needs the private-repository note removed.

The transform never edits the committed README in this repository unless
``--in-place`` is passed (intended for CI workspaces only).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REPO = "https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST"
RAW_ROOT = (
    "https://raw.githubusercontent.com/multi-agent-systems-failure-taxonomy/AdaMAST/main"
)

_NOTE_MARKER = "Private development repository"
_MD_LINK = re.compile(r"(\[[^\]]*\]\()(?!https?://|#|mailto:)([^)\s]+)(\))")
_HTML_SRC = re.compile(r'(src=")(?!https?://)([^"]+)(")')


def strip_private_note(text: str) -> str:
    """Remove the leading blockquote that marks the private repository."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(">") and _NOTE_MARKER in "".join(lines[index : index + 2]):
            while index < len(lines) and lines[index].startswith(">"):
                index += 1
            while index < len(lines) and lines[index].strip() == "":
                index += 1
            continue
        out.append(line)
        index += 1
    return "".join(out)


def _absolute(path: str, *, raw: bool) -> str:
    clean = path
    while clean.startswith("./"):
        clean = clean[2:]
    anchor = ""
    if "#" in clean:
        clean, anchor = clean.split("#", 1)
        anchor = f"#{anchor}"
    if raw:
        return f"{RAW_ROOT}/{clean}"
    # A trailing slash always marks a directory link; the filesystem check
    # is only a fallback for slash-less directory links. Relying on the
    # filesystem alone made the result depend on which checkout runs the
    # transform (the public mirror excludes some linked directories).
    is_dir = clean.endswith("/") or (REPO_ROOT / clean).is_dir()
    kind = "tree" if is_dir else "blob"
    return f"{PUBLIC_REPO}/{kind}/main/{clean}{anchor}"


def absolutize_links(text: str) -> str:
    """Point relative links at the public repository and images at raw URLs."""
    text = _HTML_SRC.sub(lambda m: f"{m.group(1)}{_absolute(m.group(2), raw=True)}{m.group(3)}", text)

    def replace(match: re.Match[str]) -> str:
        path = match.group(2)
        is_image = match.group(1).startswith("![") or path.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".svg")
        )
        return f"{match.group(1)}{_absolute(path, raw=is_image)}{match.group(3)}"

    return _MD_LINK.sub(replace, text)


def transform(text: str, *, pypi: bool) -> str:
    text = strip_private_note(text)
    if pypi:
        text = absolutize_links(text)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readme", nargs="?", default=str(REPO_ROOT / "README.md"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pypi", action="store_true", help="strip the note and absolutize links")
    mode.add_argument("--strip-note-only", action="store_true", help="only strip the private note")
    parser.add_argument("--in-place", action="store_true", help="rewrite the file (CI workspaces)")
    parser.add_argument("--output", type=Path, help="write the result here instead")
    args = parser.parse_args(argv)

    source = Path(args.readme)
    result = transform(source.read_text(encoding="utf-8"), pypi=args.pypi)
    if args.in_place:
        source.write_text(result, encoding="utf-8", newline="\n")
        print(f"rewrote {source}")
    elif args.output:
        args.output.write_text(result, encoding="utf-8", newline="\n")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
