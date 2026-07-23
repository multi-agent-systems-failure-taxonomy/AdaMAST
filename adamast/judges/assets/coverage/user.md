## TAXONOMY CATALOG
$taxonomy_catalog

## OBSERVATION

### failure_point
$failure_point

### trace (supporting context)
$trace_text

## OUTPUT (JSON only)

{
  "coverage_status": "covered | partially_covered | not_covered",
  "closest_codes": ["A.3", "B.1"],
  "missing_failure_pattern": "one-sentence description of what is not captured (or null)",
  "suggest_new_code": false,
  "proposed_failure_mode": null
}

Rules:
  1. coverage_status MUST be one of: covered, partially_covered, not_covered.
  2. closest_codes MUST reference codes present in the taxonomy catalog above.
  3. If coverage_status is "covered", missing_failure_pattern MUST be null
     and suggest_new_code MUST be false.
  4. If suggest_new_code is true, proposed_failure_mode MUST be a non-null
     object with name + definition (detection_heuristics optional).

Return ONLY the JSON object.
