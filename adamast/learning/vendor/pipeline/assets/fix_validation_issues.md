Fix these Category ${category} codes that have validation issues.

CATEGORY ${category} RULES:
${naming_rule}
${applies_rule}
- Definition: concise and clear
- detection_heuristics: required (as many as needed for clarity)
- when_to_use and when_not_to_use: required

TRACE FIELDS TO REFERENCE: ${fields_str}

CODES TO FIX:
${codes_to_fix}

Fix all issues. Keep code IDs the same.

OUTPUT JSON:
{
  "fixed_codes": [...]
}
