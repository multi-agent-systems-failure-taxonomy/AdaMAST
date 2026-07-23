# adamast/learning/

Taxonomy learning: generating a taxonomy from traces, refining it as more
traces arrive, and the durable job records that make both safe to run in the
background.

## Programs

| File | Purpose |
|---|---|
| [`api.py`](api.py) | The public `generate_taxonomy` / `judge_trace` entry points |
| [`generation.py`](generation.py) | Taxonomy generation from accumulated traces (`-m adamast.learning.generation` worker) |
| [`refinement.py`](refinement.py) | Cadence-based refinement review of the active taxonomy (`-m adamast.learning.refinement` worker) |
| [`reflection_refinement.py`](reflection_refinement.py) | End-of-generation reflection pass over the candidate |
| [`import_generation.py`](import_generation.py) | `adamast import-traces`: build a taxonomy from an existing trace folder |
| [`register_taxonomy.py`](register_taxonomy.py) | Validating and atomically activating a taxonomy into the store |
| [`learning_jobs.py`](learning_jobs.py) | Durable learning-job records, claims, and background worker spawning |
| [`worker_contract.py`](worker_contract.py) | The frozen prompt + signed receipt contract for native host workers |
| [`pipeline/`](pipeline/) | The ported public draft + agreement generation engines |
| [`vendor/`](vendor/) | The vendored research pipeline (provenance in [`vendor/VENDORED.md`](vendor/VENDORED.md)) |

Learning prompt assets live in [`assets/`](assets/).
