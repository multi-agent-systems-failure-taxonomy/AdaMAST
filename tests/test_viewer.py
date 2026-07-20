from __future__ import annotations

import json
from pathlib import Path

from adamast.viewer import render_taxonomy_html


def test_renders_one_read_only_taxonomy(tmp_path: Path) -> None:
    taxonomy = tmp_path / "taxonomy.json"
    taxonomy.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "strategy": "baseline",
                "status": "accepted",
                "display_name": "Demo taxonomy",
                "domain": "Demo agents",
                "summary": "A compact test taxonomy.",
                "codes": [
                    {
                        "id": "A.1",
                        "name": "Empty output",
                        "description": "The agent returned no output.",
                        "category": "A",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output = render_taxonomy_html(
        taxonomy,
        manifest={
            "acceptance": {
                "final_kappa": 0.82,
                "final_coverage": 0.91,
            }
        },
    )
    html = output.read_text(encoding="utf-8")

    assert output == taxonomy.with_suffix(".html")
    assert "Demo taxonomy" in html
    assert "Empty output" in html
    assert "Read-only local artifact" in html
    assert "activate taxonomy" not in html.lower()
    assert "__ADAMAST_TAXONOMY_PAYLOAD__" not in html


def test_escapes_script_terminator_in_embedded_taxonomy(tmp_path: Path) -> None:
    output = render_taxonomy_html(
        {
            "display_name": "Safe taxonomy",
            "codes": [
                {
                    "id": "A.1",
                    "name": "Untrusted label",
                    "description": "</script><script>globalThis.injected=true</script>",
                    "category": "A",
                }
            ],
        },
        output=tmp_path / "safe.html",
    )

    html = output.read_text(encoding="utf-8")

    assert "</script><script>globalThis.injected" not in html
    assert "<\\/script><script>globalThis.injected" in html
