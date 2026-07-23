# AdaMAST pre-submission gate

Before declaring the task complete, compare the full task trajectory and
verification evidence against the active failure-mode taxonomy.

Return one of:

- `READY_TO_SUBMIT` when no unresolved taxonomy-relevant issue remains.
- `REPAIR_REQUIRED` when one or more issues remain.

If repair is required, address the highest-impact unresolved issue, verify the
repair, and run this gate again. Perform at most $max_retries repair attempts.
After $max_retries unsuccessful attempts, stop repairing and report the
remaining issue honestly instead of claiming clean success.

Final gate format:

- `Final AdaMAST status:` READY_TO_SUBMIT | REPAIR_REQUIRED
- `Codes checked:` relevant taxonomy ids, or none
- `Evidence:` concrete task or verification evidence
- `Repair attempts used:` 0-$max_retries
- `Final decision:` submit | repair | report unresolved
