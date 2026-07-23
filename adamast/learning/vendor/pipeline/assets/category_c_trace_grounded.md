${common_header}

YOUR TASK (Trace-Grounded Stage):
Analyze the SAMPLE TRACES below. For each trace where reasoning appears flawed,
identify WHAT TYPE of reasoning error is present. Then cluster these into distinct
categories of reasoning failure.

Focus on patterns of flawed reasoning that are DETECTABLE from the trace content:
- Internal contradictions within the reasoning
- Unjustified logical leaps or unsupported claims
- Misapplication of domain concepts or techniques
- Gaps in case analysis or missing considerations
- Incorrect manipulation of domain-specific objects (formulas, data structures, etc.)
- Errors in algebraic or symbolic transformations (sign errors, invalid cancellations)
- Wrong direction of inequalities or estimates
- Geometric/spatial reasoning errors (wrong angle relations, invalid similarity claims)
- Proof structure errors (proving only one direction, assuming the conclusion)
- Logical errors (quantifier confusion, affirming the consequent)

Do NOT generate codes for:
- System-level failures (timeouts, crashes, truncation) — those are A codes
- Agent role failures (weak validation, wrong routing) — those are B codes
- Outcome-level judgments ("answer is wrong") — C codes describe the PROCESS flaw

${domain_error_ctx}
${domain_ctx}
