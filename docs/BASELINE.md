# BASELINE

**BASELINE** is AdaMAST's first named taxonomy-generation strategy. It combines
basic trace-to-taxonomy generation with the full inter-annotator agreement
process. It is deliberately separate from future runtime-specific generation.

## Process

1. Validate and normalize every supplied trace.
2. Generate a layered A/B/C draft taxonomy.
3. Adapt the draft's full layer to the agreement schema.
4. Run four annotators through:
   - independent error discovery;
   - error reconciliation;
   - A/B/C failure typing;
   - code assignment and validation; and
   - bounded code deliberation.
5. Track learned rules, anchors, confusion pairs, agreement, and coverage.
6. Refine low-agreement definitions for up to five rounds.
7. Mark the result `accepted` only when both gates pass.

The default gates are:

- macro Fleiss κ over used codes: **0.75**
- error coverage: **0.70**
- maximum agreement rounds: **5**
- maximum deliberation rounds per disagreement: **2**

## Generate

Choose any supported provider. The generation strategy and prompt content stay
the same:

```powershell
$env:OPENAI_API_KEY = "..."
adamast taxonomy generate `
  --strategy baseline `
  --provider openai `
  --model gpt-5-nano `
  --traces .\traces `
  --output .\run `
  --view
```

OpenAI, Anthropic, Google Gemini, and AWS Bedrock setup and command examples
are documented in [PROVIDERS.md](PROVIDERS.md).

`--view` is optional. It creates `taxonomy.html` and opens it in the default
browser. The HTML is a self-contained, read-only view of this one taxonomy.

## Output contract

```text
run/
├── taxonomy.json
├── taxonomy.html
├── taxonomy.draft.json
├── manifest.json
└── artifacts/
    ├── inputs/
    │   ├── traces.normalized.jsonl
    │   └── trace_report.json
    ├── draft/
    └── agreement/
```

`manifest.json` records the strategy, provider, model, input report,
thresholds, observed metrics, status, and artifact paths. A completed run can
have either:

- `accepted`: κ and coverage both passed; or
- `review_required`: artifacts were produced but the agreement gate failed.

The latter is never silently presented as an accepted taxonomy.

## View an existing result

```powershell
adamast taxonomy view .\run\taxonomy.json
```

The viewer has no activation action, runtime state, trace polling, or taxonomy
selection behavior.

## Provenance

The draft and agreement engines were migrated from the tracked
`olympiad-agents/Set-Up programs/1_taxonomy_generation` implementation. The
original repositories remain untouched and serve as migration references.
Exact source paths and commits are recorded in
[PROVENANCE.md](PROVENANCE.md).
