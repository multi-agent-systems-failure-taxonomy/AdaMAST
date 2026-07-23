Analyze these ${category_context} for semantic overlaps.

Two codes OVERLAP if they describe the same underlying failure mode, even if
worded differently. Also flag cases where one code is a SYMPTOM of another.

CODES:
${codes}

For each overlap pair, explain why they overlap and which should be kept.
If no overlaps exist, return an empty list.

OUTPUT JSON:
{
  "overlaps": [
    {
      "code1": "${category}.X",
      "code2": "${category}.Y",
      "reason": "Both describe the same failure: ...",
      "recommendation": "Keep ${category}.X, merge ${category}.Y into it"
    }
  ]
}
