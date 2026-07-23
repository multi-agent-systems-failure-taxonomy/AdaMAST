You are the ${stage_name} Agent generating Category ${category} codes.

${stage_prompt}
${existing_str}
${traces_section}

${requirements}

OUTPUT JSON:
{
  "codes": [
    {
      "code": "${category}.X",
      "name": "Descriptive_Name",
      "definition": "Concise definition.",
      "when_to_use": "When to apply",
      "when_not_to_use": "When NOT to apply",
      "detection_heuristics": ["observable signal from trace content", "..."],
      "severity": "critical|major|minor"${evidence_field}${b_extra}
    }
  ]
}
