CATEGORY A - System Failures (Agent-Independent)

These are failures that can happen to ANY agent regardless of role.
NOT about correctness — about system-level issues that prevent agents from
functioning properly or producing usable output.

NAMING RULE: A-codes must NEVER contain agent role names (${role_str}).
ROLE-NEUTRALITY RULE: A-codes must describe GENERIC system failures, not failures specific to
one agent's purpose. Apply the "swap test": if replacing the agent with a different-role agent
would make the code inapplicable, it belongs in B, not A. For example:
  - GOOD A code: "Output truncation" — any agent can produce truncated output
  - GOOD A code: "Inter-agent information loss" — any handoff can lose information
  - BAD A code: "Verdict misreporting" — only a checker produces verdicts -> this is B
  - BAD A code: "Refinement inconsistency" — only a refiner refines -> this is B
