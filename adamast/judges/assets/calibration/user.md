## TAXONOMY CATALOG
$taxonomy_catalog

## ANNOTATION TO AUDIT
$annotation

## CITED EVIDENCE
$evidence

## OUTPUT (JSON only)

{
  "annotation_valid": true,
  "evidence_support": "strong | moderate | weak | none",
  "possible_overtrigger": false,
  "conflicting_codes": ["A.5"],
  "rationale": "one paragraph explaining the verdict"
}

Rules:
  1. evidence_support MUST be one of: strong, moderate, weak, none.
  2. annotation_valid=true requires evidence_support in (strong, moderate).
  3. conflicting_codes MUST reference codes present in the taxonomy catalog.
  4. rationale MUST be a non-empty string.

Return ONLY the JSON object.
