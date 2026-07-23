${common_header}

YOUR TASK (Domain-Seeded Stage):
Using the domain error patterns, subdomains, and terminology pitfalls below as scaffolding,
generate categories of reasoning failure that:
1. Are DETECTABLE from the trace alone — a judge reading the reasoning can spot the flaw
   without solving the problem independently
2. Describe ERROR TYPES, not error instances — each code should apply across many problems,
   not just one specific scenario
3. Are at the right GRANULARITY — not too broad ("mathematical error") and not too narrow
   ("forgot to check n=0 in induction")
4. COVER ALL SUBDOMAINS — ensure each subdomain's characteristic reasoning failures are
   represented. Don't cluster all codes around one subdomain while ignoring others.

COVERAGE CHECK: After generating codes, verify you have at least one code relevant to
each subdomain listed below. If a subdomain has no coverage, add a code for its most
common reasoning failure type.

For each code, provide a concrete example of when it applies vs when it does NOT,
to ensure the code is operationally distinguishable from other codes.

DISTINGUISHABILITY RULE: If two codes cannot be told apart by a judge reading a trace,
they must be merged into one code.

${domain_error_ctx}
${domain_ctx}
