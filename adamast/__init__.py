"""AdaMAST: adaptive failure-mode taxonomies from agent traces.

Quickstart
----------

.. code-block:: python

    from adamast import generate_taxonomy, judge_trace

    taxonomy = generate_taxonomy(
        "traces.jsonl",
        "./run",
        provider="openai",
        model="gpt-5-nano",
    )
    print(taxonomy["status"], len(taxonomy["codes"]))

    diagnosis = judge_trace(
        taxonomy,
        {
            "problem_id": "new-trace",
            "raw_trajectory": "The agent called the wrong tool.",
        },
        provider="openai",
        model="gpt-5-nano",
    )
    print(diagnosis.code, diagnosis.evidence)
"""

from .api import (
    build_public_taxonomy,
    generate_taxonomy,
    prepare_taxonomy_for_agreement,
)
from .judges import (
    Diagnosis,
    JudgeResponseError,
    TaxonomyJudge,
    create_judge,
    judge_trace,
    judge_traces,
)
from .traces import load_trace_bundle, load_traces, write_normalized_jsonl
from .viewer import render_taxonomy_html

__all__ = [
    "__version__",
    "generate_taxonomy",
    "build_public_taxonomy",
    "prepare_taxonomy_for_agreement",
    "Diagnosis",
    "JudgeResponseError",
    "TaxonomyJudge",
    "create_judge",
    "judge_trace",
    "judge_traces",
    "load_trace_bundle",
    "load_traces",
    "write_normalized_jsonl",
    "render_taxonomy_html",
]
__version__ = "0.2.0.dev0"
