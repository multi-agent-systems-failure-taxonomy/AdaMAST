# adamast/judges/

The seven taxonomy-aware judges `adamast` exposes. Each consumes a taxonomy
and produces a different structured signal.

The `adamast judge` CLI and the `create_judge`/`judge_trace`/`judge_traces`
API are served by [`contract.py`](contract.py), the provider-neutral JUDGES
layer. Its default mode wraps the Selection judge (row 1) behind the shared
provider adapters; `--mode single` (or `mode="single"`) runs `TaxonomyJudge`,
the contract-layer judge that classifies each trace into exactly one
best-supported code. `TaxonomyJudge` lives only in `contract.py`: it carries
its own prompt and strict output validation and is not part of the
asset-driven family in the table below.

| # | Judge | Status | Implementation | Purpose |
|---|---|---|---|---|
| 1 | Selection | **real** | [`simple.py`](simple.py) + [`assets/selection/`](assets/selection/) | trace + taxonomy -> flat failure-mode labels (shallow, scalable; the default `adamast judge` mode) |
| 2 | Reflection | **real** | [`reflection_judge/`](reflection_judge/) | trace + taxonomy -> failure-point causal graph + taxonomy mappings (deep; for mutation/repair) |
| 3 | Mapping | **real** | [`simple.py`](simple.py) + [`assets/mapping/`](assets/mapping/) | failure_point + taxonomy -> best code(s) (modular sub-judge) |
| 4 | Coverage | **real** | [`simple.py`](simple.py) + [`assets/coverage/`](assets/coverage/) | trace/failure_point + taxonomy -> covered / partially / missing (drives expansion) |
| 5 | Quality | **real** | [`simple.py`](simple.py) + [`assets/quality/`](assets/quality/) | taxonomy + support traces -> codebook quality feedback (evaluates codes, not traces) |
| 6 | Calibration | **real** | [`simple.py`](simple.py) + [`assets/calibration/`](assets/calibration/) | annotation + evidence + taxonomy -> reliability of a code assignment (audits Selection) |
| 7 | Selection-Summary | **real** | [`selection_summary_judge.py`](selection_summary_judge.py) | labeled failures -> compressed selection signal (root/attributable/unrecovered/terminal/actionable/external buckets) |

## Shared shape

The five simple LLM judges are natural-language judge assets run by
`JudgeController` in [`simple.py`](simple.py). This gives two supported usage
paths:

- Python controller: instantiate `JudgeController("selection", taxonomy, ...)`
  or the compatibility classes (`SelectionJudge`, `MappingJudge`, etc.).
- Direct natural-language import: read the relevant `assets/<judge>/system.md`
  and `assets/<judge>/user.md` from another harness, including Claude Code.

Every Python-run simple judge follows the same shape so it remains composable
and testable:

- A required `judge_model` constructor argument and an optional `llm_call`
  injection point for tests (matches the `(prompt, model) -> raw_text`
  signature used by `adamast.llm.learning_calls.support_model_call`).
- A `run(...)` method returning a frozen dataclass result with a
  `judge_metadata` dict (judge name, model, taxonomy version, timestamp,
  warnings collected during the call).
- Structural + enum validation in Python; the controller salvages partial
  output rather than crashing on minor schema misses.

All Python-run simple judges route their LLM call through
`adamast/llm/learning_calls.py::judge_json`, which already handles JSON
repair-retry and routes to Anthropic (incl. Bedrock) / OpenAI / Gemini based
on the model id.

The Selection-Summary judge is intentionally the exception: it has no prompt
asset because it is deterministic Python compression of Reflection Judge output,
not an LLM call.

## Real implementations

- **Selection Judge**: shallow per-trace classifier for scalable
  taxonomy-code assignment. Standalone: no `ProgramWorkspace` required.
- **Reflection Judge**: deep multi-stage trace analyzer that identifies
  failure points, builds causal structure, and maps supported points to
  taxonomy codes.
- **Mapping Judge**: single-failure-point code assignment. Mirrors the
  Reflection Judge's two-call Stage 8 but standalone.
- **Coverage Judge**: given a trace and/or failure point, decides whether
  the taxonomy covers / partially covers / misses the pattern; proposes a new
  code when warranted.
- **Quality Judge**: evaluates the taxonomy itself (codes for observability,
  overlap, scope, clarity). Optional support traces ground recommendations in
  evidence.
- **Calibration Judge**: audits a Selection-Judge annotation against the
  cited evidence; flags weak-evidence/high-confidence mismatches and possible
  over-triggers.
- **Selection-Summary Judge**: deterministic compression of Reflection Judge
  output into root/attributable/unrecovered/etc. buckets.

The Reflection Judge is also the validation gate used by
`adamast/learning/reflection_refinement.py` at the end of generation.

## How each judge fits in a generation+refinement run

```text
Selection Judge   -> cheap per-trace classification (scoring, statistics, CI)
Reflection Judge  -> per-trace deep analysis (causal graph + mappings)
                    drives end-of-generation refinement (add / edit / split / retire)
Mapping Judge     -> standalone code assignment for one failure point
Coverage Judge    -> fast yes/no on whether the taxonomy covers a new failure
Quality Judge     -> periodic codebook self-evaluation
Calibration Judge -> audit Selection-Judge annotations for evidence support
Selection-Summary -> deterministic compression of Reflection output
```
