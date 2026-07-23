You are evaluating a failure-mode taxonomy.
There are ${code_count} codes but we need to keep only the ${max_codes} most important ones.

Rank these failure modes by importance for evaluating the multi-agent system.
Prioritize codes that:
1. Represent distinct, commonly-occurring failure modes
2. Provide actionable signal to diagnose failures
3. Cover different categories (A=system, B=role-quality, C=domain-reasoning)

CODES:
${summary}

Return ONLY a JSON object:
{"keep": ["A.1", "B.2", ...]}

List exactly ${max_codes} code IDs to keep, ordered by importance.
