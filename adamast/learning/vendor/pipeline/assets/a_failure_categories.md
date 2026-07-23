When generating Category A (System Failure) codes, consider these failure categories.
Not all categories will apply to every system — generate codes only for categories
that are relevant based on the architecture and trace evidence.

1. OUTPUT ISSUES: Agent produces no output, partial output, garbled output, or
   output that cannot be used by downstream agents. Consider: empty responses,
   truncated mid-sentence, malformed structure, output that doesn't match
   expected format for the system.

2. CONTEXT / MEMORY ISSUES: Agent loses track of prior information, contradicts
   its own earlier reasoning, forgets constraints, or cannot process all input
   because it exceeds capacity. Consider: context window overflow, information
   loss across long traces, re-deriving already established facts.

3. INTER-AGENT COMMUNICATION ISSUES: Information is lost, corrupted, or
   misrouted between agents. Consider: handoff failures, information not
   properly passed to next stage, downstream agent missing upstream context,
   miscommunication between agents.

4. BEHAVIORAL ANOMALIES: Agent exhibits pathological behavior patterns.
   Consider: repetitive/looping output, circular reasoning, refusal to engage,
   abandonment mid-task, degrading output quality over the course of the trace.

5. EXECUTION ERRORS: System-level failures during agent execution. Consider:
   timeouts, crashes, API errors, rate limiting, resource exhaustion, runtime
   exceptions visible in the trace.

6. INSTRUCTION COMPLIANCE: Agent fails to follow its system prompt or task
   instructions. Consider: ignoring constraints, responding to a different
   problem than asked, not adhering to output format requirements specified
   in the prompt.

7. TOOL / API INTERACTION ISSUES: Agent fails when invoking external tools,
   APIs, or function calls. Consider: calling wrong tool for the task,
   passing incorrect or malformed arguments, misinterpreting tool response
   data, tool returning errors that agent doesn't handle, agent retrying
   failed tool calls without adjusting, agent ignoring tool results.
   This applies to any system where agents interact with external tools,
   databases, or APIs as part of their workflow.

IMPORTANT GUIDELINES:
- Generate codes that describe CAUSES, not just symptoms. "Token limit caused
  truncation" is better than "output is missing its ending."
- Keep codes format-agnostic — they should apply regardless of specific trace
  delimiters or markers used by this particular system.
- Each code should represent a genuinely distinct failure mode. Do NOT generate
  multiple codes that describe variants of the same underlying problem.
- If a failure mode is plausible based on the architecture but not observed in
  traces, include it and mark evidence as "theoretical".
