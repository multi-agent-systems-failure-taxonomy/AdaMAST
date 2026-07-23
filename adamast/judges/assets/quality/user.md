## TAXONOMY CATALOG
$taxonomy_catalog

$support_traces_section## OUTPUT (JSON only)

{
  "code_quality": [
    {
      "code": "A.3",
      "issue": "overlaps with A.5 in detection heuristics; both fire on the same evidence",
      "recommendation": "merge A.3 into A.5, OR sharpen A.3 to cover only premature termination not invalid finalization"
    }
  ],
  "overall_quality": "good | needs_refinement | poor",
  "overall_summary": "one-paragraph summary of taxonomy health"
}

Rules:
  1. Only include codes WITH a real issue. Healthy codes get omitted.
  2. Codes you reference MUST be present in the taxonomy catalog above.
  3. overall_quality MUST be one of: good, needs_refinement, poor.
  4. overall_summary MUST be a non-empty string explaining the verdict.

Return ONLY the JSON object.
