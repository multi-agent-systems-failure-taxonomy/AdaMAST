Validate codes against strict category rules.

DISCOVERED AGENTS: ${agent_names}
ROLE TYPES: ${role_names}

VALIDATION RULES:
A: System failures - NO role names in code name, about mechanical failures
B: Role quality failures - MUST have role name in code name, about incorrect work
C: Reasoning failures - NO role names, about domain-specific logic errors

CATEGORY A:
${a_codes}

CATEGORY B:
${b_codes}

CATEGORY C:
${c_codes}

Fix any violations. Move misplaced codes to correct category.

OUTPUT JSON:
{
  "violations_fixed": [
    {
      "code": "X.Y",
      "issue": "description of the problem",
      "action": "what was done",
      "move_to": "a|b|c or null if no move needed",
      "new_name": "updated name or null if unchanged",
      "applies_to_role": "role name or null if unchanged"
    }
  ]
}
