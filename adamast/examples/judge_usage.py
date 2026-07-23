"""Apply one accepted AdaMAST taxonomy to new traces.

Set the provider credential first, then run from the repository root:

    python -m adamast.examples.judge_usage
"""

from adamast import create_judge, load_traces


taxonomy_path = "examples/taxonomy.sample.json"
traces = load_traces("examples/traces.jsonl")

# The default judge selects every failure code the trace evidence supports —
# zero, one, or several per trace.
judge = create_judge(
    taxonomy_path,
    provider="openai",
    model="gpt-5-nano",
)

for diagnosis in judge.judge_many(traces):
    if diagnosis.none_apply:
        print(diagnosis.trace_id, "none apply")
        continue
    for mode in diagnosis.failure_modes:
        print(diagnosis.trace_id, mode["code"], mode.get("evidence", ""))

# For exactly one best-supported code per trace, use the single-code judge:
single = create_judge(
    taxonomy_path,
    provider="openai",
    model="gpt-5-nano",
    mode="single",
)

for diagnosis in single.judge_many(traces):
    print(
        diagnosis.trace_id,
        diagnosis.code,
        diagnosis.label,
        diagnosis.confidence,
    )
