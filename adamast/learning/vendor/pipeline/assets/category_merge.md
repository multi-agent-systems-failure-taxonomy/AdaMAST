Deduplicate these Category ${category} codes.

ALL GENERATED CODES (from two analysis stages):
${all_codes}

DEDUPLICATION RULES:
1. Two codes are duplicates if they describe the SAME underlying failure, even if
   worded differently. E.g., "output missing" and "no output produced" are duplicates.
2. If one code describes a CAUSE and another describes its SYMPTOM, keep the CAUSAL
   code and remove the symptom code. E.g., keep "token limit exhaustion" over
   "output truncated mid-sentence" — the truncation is a symptom of the token limit.
3. When merging duplicates, prefer the code with more specific detection_heuristics
   or the one with "evidence": "observed" over "evidence": "theoretical".
4. Be aggressive about merging — it is better to have fewer distinct codes than
   many overlapping ones.

OUTPUT JSON:
{
  "kept_codes": [{"name": "...", "definition": "..."}],
  "removed": [{"name": "...", "reason": "Duplicate of / symptom of ..."}]
}
