# BASELINE source provenance

BASELINE was copied into this repository in a reviewable migration. No source
repository was deleted, moved, renamed, or edited.

## Draft generator

- Source repository: `olympiad-agents`
- Source commit: `67fbe490c`
- Source path:
  `Set-Up programs/1_taxonomy_generation/LLM_Nomos.py`
- AdaMAST path:
  `src/adamast/generation/baseline/draft.py`

## Agreement process

- Source repository: `olympiad-agents`
- Source history begins at: `b17e6261d`
- Copied from commit: `67fbe490c`
- Source path:
  `Set-Up programs/1_taxonomy_generation/MATRS_taxonomy_refiner.py`
- Equivalent historical copy:
  `mas_taxonomy_refiner.py`
- AdaMAST path:
  `src/adamast/generation/baseline/agreement.py`

## Migration changes

The copied engines retain their original prompts and phase logic. BASELINE adds
the following integration code around them:

- a named generation-strategy contract;
- one shared trace normalizer and validator;
- a layered-draft to agreement-schema adapter;
- explicit acceptance status and run manifests;
- stable public taxonomy output;
- a read-only standalone taxonomy viewer; and
- deterministic tests that do not call an external model.

The public `ATLAS` repository and local `ATLAS`/`atlas_skill` checkouts remain
backup/reference sources during this migration.
