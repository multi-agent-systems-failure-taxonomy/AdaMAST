Review codes across all three categories for semantic duplicates.

CATEGORY RULES:
A: System failures - agent-independent (can happen to ANY agent)
B: Role-specific QUALITY failures - WHO did their job wrong
C: Domain reasoning failures - WHY the reasoning is wrong

CRITICAL BOUNDARY RULES — read carefully before marking duplicates:
1. An A code and a B code that describe the SAME EVENT from different levels of analysis
   are NOT duplicates. Example: "Inter-agent information loss" (A) and "Checker performs
   weak validation" (B) may co-occur but describe different things — the system-level
   symptom vs the role-specific cause.
2. A B code is a duplicate of another B code ONLY if they describe the same quality failure
   for the same role. B codes for DIFFERENT roles are never duplicates of each other.
3. A C code is a duplicate of another code ONLY if it describes the exact same reasoning
   error type. A C code describing a reasoning flaw is NOT a duplicate of a B code
   describing a role doing its job poorly, even if the reasoning flaw is what caused
   the role failure.
4. Cross-category duplicates (A<->B, A<->C, B<->C) should be RARE. Only mark as duplicate
   when codes are truly synonymous — same concept, same granularity, same perspective.

CATEGORY A:
${a_codes}

CATEGORY B:
${b_codes}

CATEGORY C:
${c_codes}

Find semantic duplicates. Each concept in exactly ONE category.
Remember: cross-category duplicates should be rare. Most duplicates will be WITHIN a category.

OUTPUT JSON:
{
  "duplicates_found": [{"concept": "...", "found_in": ["A.1", "B.3"], "keep_in": "A.1", "remove": "B.3"}]
}
