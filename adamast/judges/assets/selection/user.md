## TAXONOMY CATALOG
$taxonomy_catalog

## TRACE
$trace_text

## OUTPUT (JSON only)

{
  "failure_modes": [
    {
      "code": "A.3",
      "name": "Premature termination",
      "evidence": "<quote or paraphrase from the trace>",
      "confidence": "high|medium|low",
      "severity": "minor|moderate|major|critical"
    }
  ],
  "none_apply": false
}

Rules:
  1. Every entry in failure_modes MUST have a non-empty evidence string.
  2. If you populate failure_modes, none_apply MUST be false.
  3. If none_apply is true, failure_modes MUST be [].
  4. Codes you choose MUST be present in the taxonomy catalog above.

Return ONLY the JSON object.
