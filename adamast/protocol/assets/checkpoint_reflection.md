AdaMAST $gate_label — reflection required before this boundary can pass.

Checkpoint ID: $checkpoint_id
Active taxonomy: $taxonomy_id

Failure modes to consider:
$code_list

Scope: $scope.

Recent trajectory excerpt:
--- begin recent activity ---
$recent_activity
--- end recent activity ---

Work the steps IN ORDER. Do not consult the failure-mode list above until the
Map step: first identify what actually went wrong or what expected step is
missing, then label those failure points with taxonomy codes. This keeps the
labels from biasing what you look for.

AdaMAST reflection:
- Checkpoint ID: $checkpoint_id
- Observe: as a neutral third-person reviewer of the scoped activity, list the
  concrete failure points: specific things that went wrong, are weak, or are
  missing. Each needs verbatim evidence, either a quote or a concrete fact from
  the trace. Then do one forward sweep for expected steps that are absent, such
  as missing verification, no alternate search, repeated plateaued strategy, or
  no problem decomposition. If the work is genuinely clean, say exactly what you
  checked.
- Correlate: for each failure point, look earlier in the trace for its cause
  and name the root one. Only assert a cause the trace shows.
- Map: now consult the failure modes above and label each failure point:
  - `<CODE> | evidence: "<verbatim fact from that failure point>"`
  - multiple codes are allowed, and the same code may recur on different points
  - only if you found no failure points at all:
    `none apply | considered: <CODE,...> | evidence: "<why the work is genuinely clean>"`
- Decide: in first person, address the highest-value point, exactly one of:
  - `change: <one focused change>`
  - `no change needed, because <evidence-based reason>`

Replacement standard: if the change would replace a result or answer you
already produced, a committed answer may be replaced only by demonstrating
its own failure — never by demonstrating an alternative's appeal. Construct
and run a check against the current answer: recompute it from its own stated
inputs, re-read the value at its cited source location, re-check a constraint
the task text explicitly states, or confirm a step the task requires never
happened. Compare the results yourself. If you cannot construct a failing
check, keep the answer, however compelling another reading looks.

Find failure points from the work itself, not by scanning the code list for
matches. A better result than before is not evidence that nothing is failing:
improving work can still have weak spots, skipped steps, or better directions
not yet tried. Judge the activity on its own terms. A well-supported
`none apply` is still valid; never manufacture a failure or a change merely to
satisfy the checkpoint.

Format guardrail: emit one complete `AdaMAST reflection:` block. Keep the
Checkpoint ID line exactly identifiable, even if you use Markdown headings.
For fired codes, each code line must contain a known code id plus an
`evidence:` phrase; for clean checkpoints, write `none apply` plus considered
code ids and evidence.$final_instructions
