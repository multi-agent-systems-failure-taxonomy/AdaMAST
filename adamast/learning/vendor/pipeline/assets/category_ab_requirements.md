REQUIREMENTS:
1. Generate codes for complete coverage of all distinct system failure modes
2. Each code must represent a genuinely distinct failure — do NOT create multiple
   codes for variants of the same problem (e.g., do not have separate codes for
   "output missing" and "output empty" — those are the same failure)
3. Prefer CAUSAL codes over SYMPTOM codes. "Token limit caused truncation" is one
   code, not two separate codes for "token limit hit" and "output truncated"
4. Each code needs detection_heuristics grounded in observable signals
5. Definitions should be concise and clear
6. Follow naming rules strictly
