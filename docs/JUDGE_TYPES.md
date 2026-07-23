# Choose a judge

This page helps you pick the smallest judge that matches the decision your
pipeline needs — and shows the minimal code to run each one.

AdaMAST separates judgment tasks because one prompt should not be expected to
perform every kind of evaluation.

## 🧭 Judge selection table

| Judge | Input | Output | Best suited for |
| --- | --- | --- | --- |
| Core trace judge | trace + taxonomy | one validated code, evidence, confidence, recovery hint | Simple standalone classification |
| Selection | trace + taxonomy | zero or more supported codes | Scalable labeling, comparison, statistics, CI |
| Reflection | trace + taxonomy | failure points, causal graph, mappings, summary | Deep diagnosis, mutation, repair, refinement |
| Mapping | one failure point + taxonomy | primary/secondary codes or a proposed missing mode | Modular code assignment |
| Coverage | trace or failure point + taxonomy | covered, partially covered, or not covered | Deciding whether the taxonomy needs expansion |
| Quality | taxonomy + optional support traces | per-code issues and overall quality | Periodic codebook review |
| Calibration | annotation + evidence + taxonomy | validity and evidence support | Auditing Selection annotations |
| Selection-Summary | Reflection output | deterministic selection buckets | Compressing a causal analysis without another model call |

The [core trace judge](JUDGING.md) is the public CLI/API path. The specialized
controllers live under `adamast/judges/` and are used by the adaptive runtime and
research pipelines.

!!! tip
    Start with the core trace judge; reach for a specialized judge only when
    the table names your exact decision.

## 🎯 Core trace judge

Choose the core judge when exactly one failure code per trace is sufficient and
you want a provider-neutral CLI or Python interface with strict output
validation.

```bash
adamast judge \
  --provider openai \
  --taxonomy ./taxonomy.json \
  --traces ./traces.jsonl
```

## 🏷️ Selection judge

Selection is a shallow, scalable multi-label classifier — it may return several
codes, or none. It returns supported failure modes with evidence, confidence
tier, and severity, or explicitly says that no mode applies.

```python
from adamast.core.taxonomy_data import Taxonomy
from adamast.judges import SelectionJudge

taxonomy = Taxonomy.from_json("./taxonomy.json")
judge = SelectionJudge(taxonomy, judge_model="gpt-5")
result = judge.run("the normalized trace text")

print(result.code_ids())
print(result.judge_metadata["warnings"])
```

Use Selection for repeated labeling. Use Calibration when those labels require
an independent evidence audit.

## 🪞 Reflection judge

Reflection is the deepest trace judge. It first identifies concrete failure
points and builds a backward-grounded causal graph without seeing the taxonomy.
Only afterward does it map supported points to taxonomy codes. This ordering
reduces the risk that the existing codebook determines what the judge notices.

```python
from adamast.core.taxonomy_data import Taxonomy
from adamast.judges.reflection_judge import AdaMASTReflectionJudge

taxonomy = Taxonomy.from_json("./taxonomy.json")
judge = AdaMASTReflectionJudge(
    taxonomy,
    judge_model="gpt-5",
)

result = judge.analyze({
    "task_id": "trace-17",
    "candidate_id": "agent-run-3",
    "run_id": "evaluation-1",
    "task_prompt": "original task",
    "candidate_output": "the submitted answer",
    "score": 0.0,
    "trace": "complete trajectory"
})
```

The default two-call mode separates analysis from mapping and then derives the
selection summary deterministically. A single-call mode and one validation
retry are also available for controlled experiments. Reflection is the
end-of-generation validation surface used by the adaptive refinement runtime.

## 🗺️ Mapping judge

Mapping classifies one already-identified failure point. It can return primary
and secondary codes, or mark the point unmapped and propose a missing failure
mode.

```python
from adamast.judges import MappingJudge

judge = MappingJudge(taxonomy, judge_model="gpt-5")
result = judge.run({
    "failure_point_id": "fp-2",
    "evidence": "the tool returned an incomplete response",
    "description": "the agent treated the partial result as complete"
})
```

Choose Mapping when another component already owns failure discovery.

## 📡 Coverage judge

Coverage decides whether a trace or failure point is `covered`,
`partially_covered`, or `not_covered`. For missing patterns it can recommend a
new code with a proposed name and definition.

```python
from adamast.judges import CoverageJudge

judge = CoverageJudge(taxonomy, judge_model="gpt-5")
result = judge.run({"trace": "...", "failure_point": {"evidence": "..."}})
print(result.coverage_status, result.suggest_new_code)
```

!!! note
    Coverage is a focused trigger for expansion; it is not a replacement for
    the four-annotator agreement metric.

## 🩺 Quality judge

Quality evaluates the codebook rather than classifying one trace. It reports
code-level observability, overlap, scope, and clarity issues plus an overall
quality result.

```python
from adamast.judges import QualityJudge

judge = QualityJudge(taxonomy, judge_model="gpt-5")
result = judge.run(support_traces=["trace one", "trace two"])
print(result.overall_quality)
```

Support traces are optional but make recommendations easier to audit.

## ⚖️ Calibration judge

Calibration audits an existing annotation against its cited evidence. It can
flag weak-evidence/high-confidence assignments, conflicting codes, and likely
over-triggers.

```python
from adamast.judges import CalibrationJudge

judge = CalibrationJudge(taxonomy, judge_model="gpt-5")
result = judge.run({
    "annotation": {"code": "A.2", "confidence": "high"},
    "evidence": "the exact supporting span from the trace"
})
```

## 🧮 Selection-Summary judge

Selection-Summary is deterministic Python compression of Reflection output. It
groups labeled failures into root, attributable, unrecovered, terminal,
actionable, and external buckets without making another model call.

Use it when consumers need a compact selection-oriented view but the full
causal result must remain available for audit.

## 🔌 Shared controller contract

The Selection, Mapping, Coverage, Quality, and Calibration judges share
`JudgeController`:

```python
from adamast.judges import JudgeController

controller = JudgeController(
    "selection",
    taxonomy,
    judge_model="gpt-5",
)
result = controller.run("trace text")
```

Each controller validates structural fields and enums, returns a frozen result
dataclass, and includes `judge_metadata` with the judge name, model, taxonomy
version, timestamp, and warnings. Minor schema omissions are salvaged where a
trustworthy partial result exists; model-call and repair behavior routes through
the runtime learning-call layer.

## 📝 Natural-language prompt assets

The five simple judge instructions are Markdown assets under
`adamast/judges/assets/<judge>/system.md` and `user.md`. A custom harness can either
use the Python controller or load/render those assets directly. Reflection has
separate staged prompt assets under `adamast/judges/reflection_judge/assets/`.

!!! warning
    Keep prompt changes versioned with evaluation results. Changing a judge
    prompt changes the measurement procedure even when the taxonomy is
    unchanged.

## ➡️ Continue with

- [Adaptive runtime](GETTING_STARTED.md) — where the specialized judges are
  used automatically.
- [Customization](CUSTOMIZATION.md) — the exact prompt asset files to edit.
