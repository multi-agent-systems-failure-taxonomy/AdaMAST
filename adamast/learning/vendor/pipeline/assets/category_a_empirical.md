${common_header}

YOUR TASK (Empirical Behavioral Analysis):
Analyze the BEHAVIORAL SIGNALS extracted from all traces (below) and the
SAMPLE TRACES to identify system failures that ACTUALLY OCCURRED.

Focus on:
- Behavioral anomalies: looping, repetition, refusal, degrading quality
- Output issues: truncation, empty output, malformed responses
- Communication issues: information lost between agents, handoff failures
- Any system-level problems visible in the trace content

Do NOT generate codes for trace FORMAT validation rules (e.g., "missing tag X"
or "wrong delimiter Y"). Focus on the underlying system failures, not their
surface-level formatting symptoms.

For each code, set "evidence": "observed" since these come from actual trace data.

${a_failure_categories}

${signal_ctx}

${arch_ctx}
${caps_ctx}
${domain_lite}
