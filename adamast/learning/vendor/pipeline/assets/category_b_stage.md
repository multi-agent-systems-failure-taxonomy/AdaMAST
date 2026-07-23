CATEGORY B - Role-Specific Quality Failures

NAMING RULE: B-codes MUST contain role name prefix (${role_name_prefixes})

ROLE DEFINITIONS:
${role_defs_text}

ACTIVE ROLES (only generate for these): ${active_roles}

DISCOVERED AGENTS PER ROLE:
${agents_per_role}

${b_guidance}

YOUR TASK (${stage_name} Stage):
${stage_task}

Generate codes for complete coverage of all distinct quality failure modes per active role.
Each code must represent a genuinely distinct failure — do NOT create multiple codes
for variants of the same quality problem.

${caps_ctx}
${arch_ctx}
${trace_ctx}
