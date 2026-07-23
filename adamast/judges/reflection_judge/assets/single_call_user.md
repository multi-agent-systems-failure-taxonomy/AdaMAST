$analysis_part

## TAXONOMY CATALOG (for Stage 8)
$taxonomy_catalog

## ADDITIONAL OUTPUT (Stage 8 — mapping)

In each failure point, ADD the fields 'taxonomy_mappings', 'unmapped',
'ruled_out_codes' (when unmapped=true), and 'proposed_failure_mode' (when
unmapped=true) per the mapping policy:

  - ALWAYS try to map an existing code first (with appropriate
    mapping_confidence).
  - Multiple codes per failure point are allowed (mark one primary, rest
    secondary).
  - unmapped=true ONLY when no existing code even partially applies, and
    you MUST then provide ruled_out_codes (>= 1) and proposed_failure_mode
    ({name, definition, detection_heuristics?}).

Return ONLY the JSON object (with both analysis and mapping fields).
