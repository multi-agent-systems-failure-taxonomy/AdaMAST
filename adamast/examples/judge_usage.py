"""Apply one accepted AdaMAST taxonomy to new traces.

Set the provider credential first, then run from the repository root:

    python -m adamast.examples.judge_usage
"""

from adamast import create_judge, load_traces


taxonomy_path = "examples/taxonomy.sample.json"
traces = load_traces("examples/traces.jsonl")

judge = create_judge(
    taxonomy_path,
    provider="openai",
    model="gpt-5-nano",
)

for diagnosis in judge.judge_many(traces):
    print(
        diagnosis.trace_id,
        diagnosis.code,
        diagnosis.label,
        diagnosis.confidence,
    )
