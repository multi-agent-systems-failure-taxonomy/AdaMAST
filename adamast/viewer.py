"""Standalone read-only browser presentation for one taxonomy."""

from __future__ import annotations

from importlib import resources
import json
from pathlib import Path
from typing import Any, Mapping
import webbrowser


def render_taxonomy_html(
    taxonomy: Path | str | Mapping[str, Any],
    *,
    manifest: Path | str | Mapping[str, Any] | None = None,
    output: Path | str | None = None,
    open_browser: bool = False,
) -> Path:
    """Render one taxonomy to a self-contained, read-only HTML document."""

    taxonomy_path: Path | None = None
    if isinstance(taxonomy, Mapping):
        taxonomy_data = dict(taxonomy)
    else:
        taxonomy_path = Path(taxonomy).expanduser().resolve()
        taxonomy_data = _read_json(taxonomy_path)

    if isinstance(manifest, Mapping):
        manifest_data = dict(manifest)
    elif manifest is not None:
        manifest_data = _read_json(Path(manifest).expanduser().resolve())
    elif taxonomy_path and (taxonomy_path.parent / "manifest.json").exists():
        manifest_data = _read_json(taxonomy_path.parent / "manifest.json")
    else:
        manifest_data = {}

    if output is None:
        if taxonomy_path:
            output_path = taxonomy_path.with_suffix(".html")
        else:
            output_path = Path.cwd() / "taxonomy.html"
    else:
        output_path = Path(output).expanduser().resolve()

    payload = {
        "taxonomy": taxonomy_data,
        "manifest": manifest_data,
    }
    template = (
        resources.files("adamast")
        .joinpath("assets", "taxonomy_viewer.html")
        .read_text(encoding="utf-8")
    )
    serialized = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = template.replace("__ADAMAST_TAXONOMY_PAYLOAD__", serialized)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    if open_browser:
        webbrowser.open(output_path.resolve().as_uri())
    return output_path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read taxonomy JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"taxonomy JSON must contain an object: {path}")
    return payload
