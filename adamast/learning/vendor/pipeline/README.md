# adamast/learning/vendor/pipeline/

The 8-step AdaMAST taxonomy generation pipeline. Driven by
`TaxonomyPipeline.run`, which threads each step's output into the next.
Each step is an LLM-driven analysis — `domain` understands the task
domain, `structure` discovers agent roles, `generator` produces candidate
codes per axis, `dedup`/`validate`/`check` clean up the result.

## Programs

| File | Step | Purpose |
|---|---|---|
| [`__init__.py`](__init__.py) | — | Package marker + pipeline exports |
| [`pipeline.py`](pipeline.py) | (orchestrator) | `TaxonomyPipeline.run` — runs steps 1-8 in order, persists intermediate JSON at each boundary |
| [`prompts.py`](prompts.py) + [`assets/`](assets/) | (shared) | Natural-language prompt asset loading/rendering plus shared role definitions used across stages |
| [`domain.py`](domain.py) | Step 1 | `SystemDomainAnalyzer` — task type, terminology, error patterns from the trace corpus |
| [`structure.py`](structure.py) | Step 2 | `TraceStructureExtractor` — agent detection, role classification, topology |
| [`generator.py`](generator.py) | Steps 3-5 | `CategoryGenerator` — A (system) / B (role-specific) / C (domain-reasoning) code generation |
| [`dedup.py`](dedup.py) | Step 6 | `CrossCategoryDeduplicator` — merge overlaps across axes |
| [`validate.py`](validate.py) | Step 7 | `CrossCategoryValidator` — coverage checks |
| [`check.py`](check.py) | Step 8 | `TaxonomyChecker` — final naming rules, overlap merges, output schema |
