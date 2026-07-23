Fix Category C (Domain Reasoning Failure) codes.

Domain: ${domain_name}

CURRENT C CODES:
${c_codes}
${gaps_str}${overlaps_str}
Return the complete list of C codes after fixes. Include:
- All existing codes (unchanged unless merged)
- Merged codes (combining overlapping pairs)
- New codes for coverage gaps

Each code needs: code, name, definition, when_to_use, when_not_to_use,
detection_heuristics (list of strings), severity (critical/major/minor).

RULES:
- C codes must NOT contain agent role names
- Each code must describe a reasoning flaw detectable from the trace alone
- Each code must be distinguishable from every other code

OUTPUT JSON:
{
  "codes": [
    {
      "code": "C.N",
      "name": "...",
      "definition": "...",
      "when_to_use": "...",
      "when_not_to_use": "...",
      "detection_heuristics": ["..."],
      "severity": "major"
    }
  ]
}
