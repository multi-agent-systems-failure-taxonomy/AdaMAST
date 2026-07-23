## FAILURE POINT
$failure_point

## TAXONOMY CATALOG
$taxonomy_catalog

## OUTPUT (JSON only)

{
  "primary_code": "C.3",
  "secondary_codes": ["B.2"],
  "mapping_confidence": 0.85,
  "mapping_rationale": "why this fits",
  "unmapped": false,
  "ruled_out_codes": [],
  "proposed_failure_mode": null
}

When `unmapped=true`:

{
  "primary_code": null,
  "secondary_codes": [],
  "mapping_confidence": 0.0,
  "unmapped": true,
  "ruled_out_codes": [
    {"code": "C.5", "reason": "why this close code does not fit"}
  ],
  "proposed_failure_mode": {
    "name": "...",
    "definition": "...",
    "detection_heuristics": ["..."]
  }
}

Return ONLY the JSON object.
