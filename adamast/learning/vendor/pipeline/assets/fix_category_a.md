Fix Category A codes: fill coverage gaps and resolve overlaps.

CURRENT A CODES:
${a_codes}

ARCHITECTURE: ${arch_summary}
ROLES: ${roles_summary}

${gap_section}
${overlap_section}

NAMING RULE: A-codes must NEVER contain agent role names (${role_str})

Return the COMPLETE updated list of A codes (existing + new, with overlaps merged).

OUTPUT JSON:
{
  "codes": [
    {
      "code": "A.X",
      "name": "Descriptive_Name",
      "definition": "Concise definition.",
      "when_to_use": "When to apply",
      "when_not_to_use": "When NOT to apply",
      "detection_heuristics": ["observable signal", "..."],
      "severity": "critical|major|minor",
      "evidence": "theoretical|observed"
    }
  ],
  "changes_made": ["Merged A.2 and A.5 into ...", "Added code for ..."]
}
