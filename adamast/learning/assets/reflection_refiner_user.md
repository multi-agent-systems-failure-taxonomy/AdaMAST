## CURRENT TAXONOMY
$catalog

## SIGNALS FROM REFLECTION JUDGE

### Proposed new codes (from unmapped failure points — judge had no existing code that fit)
Each carries: proposed_name, support_count (how many of the sampled traces
proposed this), sample_definitions, ruled_out_against (existing codes the judge
considered and ruled out, with reasons).
$add_signals

### Weak-mapped existing codes (judge mapped these with low confidence — the definition may be too narrow, too broad, or covering two distinct patterns that should be split)
$edit_signals

### Code utilization across the sample (consolidation signal)
For each existing code in the current taxonomy: times_mapped, avg/max
confidence when mapped, and frequently_co_mapped_with (codes that appear in >=
50% of this code's uses — strong duplicate signal).
- times_mapped = 0          -> never used in THIS trace sample (NOT by itself
  a reason to remove: some codes only fire for artifact-level detectors)
- max_confidence < 0.5      -> judge always forced this code
- frequently_co_mapped_with -> likely duplicate / near-duplicate -> MERGE
$utilization

## TASK
Review the signals and decide which taxonomy changes to apply.
- ADD a new code only when a proposed_name describes a genuinely uncovered
  failure mode (not a near-duplicate of an existing code).
- EDIT an existing code's name/definition when its weak mappings reveal what
  the current definition is missing or over-claiming.
- SPLIT an existing code when its weak mappings cluster into TWO or more
  distinct patterns that deserve separate codes.
- MERGE two or more codes when they are near-duplicates or specific instances
  of one more general pattern: the merged code's name/definition must still
  cover everything the source codes covered. Never delete a code outright —
  merging is the only way a code disappears, and its content survives in the
  merged code. Apply the generality requirement: codes describing single
  observed incidents (one specific function/structure/value) must be merged
  upward into mechanism-level codes even if their surface details differ and
  even if utilization statistics are empty.

Return ONLY JSON: {"add": [{"category":"A|C","name":"...","definition":"...","detection_heuristics":["..."],"gap":"..."}],"edit": [{"code":"C.3","name":"optional","definition":"optional","reason":"..."}],"split": [{"code":"C.4","reason":"...","into":[{"name":"...","definition":"...","detection_heuristics":["..."]}]}],"merge": [{"codes":["C.8","C.11"],"category":"A|C","name":"...","definition":"...","detection_heuristics":["..."],"reason":"..."}]}
