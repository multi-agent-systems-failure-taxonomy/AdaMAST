## FAILURE POINTS (from Stage 1-7)
$failure_points

## TAXONOMY CATALOG
$taxonomy_catalog

## TASK
For each failure point above, assign taxonomy code(s) per the policy. Return
ONLY JSON in this shape:

{
  "mappings_by_failure_point": [
    {
      "failure_point_id": "F1",
      "taxonomy_mappings": [
        { "code": "C.3", "name": "...", "primary_or_secondary": "primary",
           "mapping_confidence": 0.0, "mapping_rationale": "why this fits" }
      ],
      "unmapped": false,
      "ruled_out_codes": [
        { "code": "C.5", "reason": "why this close code does not fit" }
      ],
      "proposed_failure_mode": null
    }
  ]
}

If a failure point gets mapped (unmapped=false): 'ruled_out_codes' should be
[] or omitted, and 'proposed_failure_mode' should be null.

If unmapped=true: 'taxonomy_mappings' MUST be [], 'ruled_out_codes' MUST have
>= 1 entry with code+reason, and 'proposed_failure_mode' MUST be a non-null
object with 'name', 'definition', and (optionally) 'detection_heuristics'.

Return ONLY the JSON object. No commentary.
