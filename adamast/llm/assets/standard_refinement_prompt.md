You are refining an existing failure-mode taxonomy for an agent system based
on new trace evidence. Goal: SHARPEN the taxonomy so it gives the agent clearer
signal — NOT to throw codes away.

## EXISTING TAXONOMY
$existing_taxonomy

## TRACE EVIDENCE
$trace_excerpts

## REFINEMENT INSTRUCTIONS

Produce a cleaner taxonomy by performing exactly these operations:

1. **MERGE / DROP redundant codes.** If two codes describe the same underlying
   failure, keep ONE with the clearer definition and remove the duplicate. Also
   drop codes too fine-grained to be a single broader pattern.

2. **SHARPEN vague codes.** For any code whose description is generic, rewrite
   it so it names a concrete observable: what would have to appear in a trace
   for this code to fire, and what would NOT count.

3. **ADD codes only from concrete evidence.** Look at the trace excerpts. If
   you can point to a specific repeated pattern not cleanly captured by any
   existing code, add one. Do NOT invent codes from imagination.

   - A codes: system / infrastructure failures.
   - B codes: per-role quality failures.
   - C codes: domain reasoning errors.

4. **DO NOT cull codes just because they have not fired yet.** Absence of
   evidence is not evidence of absence — keep them unless rule 1 or 2 forces
   removal.

5. **PRESERVE code IDs you keep.** A code that survives MUST reappear with its
   original ID.

6. Aim for 15-30 total codes. Quality over quantity.

Return ONLY valid JSON with exactly repo, domain, and codes. Each code uses the
canonical fields id, name, description, and category, plus any existing
severity or applies_to_role. Do not allocate a taxonomy_id.
