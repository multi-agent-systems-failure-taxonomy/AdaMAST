Fix Category B codes: fill role coverage gaps and resolve overlaps.

CURRENT B CODES:
${b_codes}

${gap_section}
${overlap_section}

NAMING RULE: B-code names MUST start with the role type (${role_prefixes})
A/B BOUNDARY: B codes describe quality of work ONLY. Never describe system failures (no output, timeout, crash).

Return the COMPLETE updated list of B codes (existing + new, with overlaps merged).

OUTPUT JSON:
{
  "codes": [
    {
      "code": "B.X",
      "name": "Role_Descriptive_Name",
      "definition": "Concise definition about quality of work.",
      "when_to_use": "When to apply",
      "when_not_to_use": "When NOT to apply",
      "detection_heuristics": ["observable quality signal", "..."],
      "severity": "critical|major|minor",
      "applies_to_role": "one of the active roles"
    }
  ],
  "changes_made": ["Added RoleName_X for ...", "Merged B.2 and B.5 into ..."]
}
