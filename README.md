# AdaMAST

AdaMAST builds adaptive failure-mode taxonomies from agent traces.

This repository is being populated in named, reviewable milestones. The first
milestone is **BASELINE**:

1. normalize and validate supplied traces;
2. generate a draft A/B/C failure taxonomy;
3. run the full four-annotator agreement and refinement process;
4. save an explicit accepted or review-required result; and
5. optionally render the resulting taxonomy as a standalone browser view.

BASELINE is one taxonomy-generation strategy. It is not the generation process
for every future AdaMAST integration.

## Install for development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Set `OPENAI_API_KEY`, then validate and generate:

```powershell
adamast traces validate .\examples\traces.jsonl
adamast taxonomy generate `
  --strategy baseline `
  --traces .\examples\traces.jsonl `
  --output .\run
```

Add `--view` to create and open a read-only HTML field guide for the generated
taxonomy. It can also be opened later:

```powershell
adamast taxonomy view .\run\taxonomy.json
```

See [BASELINE](docs/BASELINE.md) and
[accepted trace formats](docs/TRACE_FORMATS.md) for the complete contracts.
