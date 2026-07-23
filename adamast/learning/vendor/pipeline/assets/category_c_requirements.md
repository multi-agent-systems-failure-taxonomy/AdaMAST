REQUIREMENTS:
1. Each code describes an ERROR TYPE (not an error instance) — it should apply across
   many problems, not just one specific scenario
2. Each code must be DETECTABLE by a judge reading the trace — the judge should NOT need
   to solve the problem independently or know the correct answer
3. Each code must be DISTINGUISHABLE from every other code — if two codes cannot be told
   apart by a judge, merge them
4. Do NOT include system failures (A codes) or role-specific failures (B codes)
5. C codes must NEVER reference agent roles (${active_roles})
6. detection_heuristics must describe what a judge would look for in the trace text
7. Definitions should be concise and clear
8. Prioritize clarity over quantity — fewer clear codes is better than many overlapping ones
