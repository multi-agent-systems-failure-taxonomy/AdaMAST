"""AdaMAST: adaptive failure-mode taxonomies from agent traces.

Quickstart
----------

.. code-block:: python

    from adamast import generate_taxonomy

    taxonomy = generate_taxonomy(
        "traces.jsonl",
        "./run",
        provider="openai",
        model="gpt-5-nano",
    )
    print(taxonomy["status"], len(taxonomy["codes"]))
"""

from .api import (
    build_public_taxonomy,
    generate_taxonomy,
    prepare_taxonomy_for_agreement,
)
from .traces import load_trace_bundle, load_traces, write_normalized_jsonl
from .viewer import render_taxonomy_html

__all__ = [
    "__version__",
    "generate_taxonomy",
    "build_public_taxonomy",
    "prepare_taxonomy_for_agreement",
    "load_trace_bundle",
    "load_traces",
    "write_normalized_jsonl",
    "render_taxonomy_html",
]
__version__ = "0.2.0.dev0"
