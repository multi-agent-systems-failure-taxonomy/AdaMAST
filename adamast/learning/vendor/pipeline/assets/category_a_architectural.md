${common_header}

YOUR TASK (Architectural Risk Analysis):
Given the system architecture below, identify ALL plausible system-level failure
modes that could occur in this pipeline. Think about:
- What happens at each handoff point? What can go wrong?
- What happens if an agent runs too long, or its context fills up?
- What happens if an agent produces no output, or garbled output?
- What if an agent contradicts itself or loops?
- What if an agent refuses to engage or abandons the task?
- What if the pipeline terminates prematurely?

You do NOT need to see traces for this — reason purely from the architecture.
Generate codes for failures that are PLAUSIBLE based on how this system is designed.

For each code, set "evidence": "theoretical" since these come from architectural
reasoning rather than observed trace data.

${a_failure_categories}

${arch_ctx}
${caps_ctx}
${domain_lite}

${signal_ctx}
