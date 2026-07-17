# Model providers

Model transport is independent from taxonomy-generation strategy. BASELINE
uses the same draft prompts, agreement prompts, thresholds, and phase logic
regardless of provider. An adapter only:

1. maps AdaMAST's system and user strings to the provider request;
2. selects the requested model;
3. maps the optional JSON-response hint where the provider supports it;
4. extracts plain response text; and
5. reports provider errors without printing credentials.

The generated taxonomy and manifest record both `provider` and `model`.
Credentials are never written to run artifacts.

## Install a provider

Install only the SDK needed for generation:

```powershell
python -m pip install "adamast[openai]"
python -m pip install "adamast[anthropic]"
python -m pip install "adamast[google]"
python -m pip install "adamast[bedrock]"
```

Use `adamast[all]` for a development environment that exercises every adapter.
The base `adamast` install still supports trace validation, normalization, and
viewing an existing taxonomy without installing a model SDK.

| Provider | Credential environment | Model environment | Extra |
|---|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `OPENAI_MODEL` | `openai` |
| Anthropic | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` | `anthropic` |
| Google Gemini | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `GEMINI_MODEL` or `GOOGLE_MODEL` | `google` |
| AWS Bedrock | `AWS_BEARER_TOKEN_BEDROCK` or the standard AWS credential chain | `BEDROCK_MODEL_ID` | `bedrock` |

Except for the default OpenAI model, a model must be supplied through
`--model` or the corresponding model environment variable. This avoids
silently changing behavior when provider model catalogs evolve.
The provider itself must be selected with `--provider` or
`ADAMAST_PROVIDER`; AdaMAST does not guess it from whichever credential happens
to be present.

## OpenAI

```powershell
$env:OPENAI_API_KEY = "..."
adamast taxonomy generate `
  --strategy baseline `
  --provider openai `
  --model <openai-model-id> `
  --traces .\traces `
  --output .\run
```

The adapter preserves BASELINE's existing Chat Completions request shape:
AdaMAST's system and user strings remain system and user messages.

## Anthropic

```powershell
$env:ANTHROPIC_API_KEY = "..."
adamast taxonomy generate `
  --strategy baseline `
  --provider anthropic `
  --model <anthropic-model-id> `
  --traces .\traces `
  --output .\run
```

The adapter uses the Messages API.

## Google Gemini

```powershell
$env:GEMINI_API_KEY = "..."
adamast taxonomy generate `
  --strategy baseline `
  --provider google `
  --model <gemini-model-id> `
  --traces .\traces `
  --output .\run
```

The adapter uses the Google Gen AI SDK and its `generate_content` interface.

## AWS Bedrock

With a Bedrock API key:

```powershell
$env:AWS_BEARER_TOKEN_BEDROCK = "..."
adamast taxonomy generate `
  --strategy baseline `
  --provider bedrock `
  --model <bedrock-model-or-inference-profile-id> `
  --aws-region us-east-1 `
  --traces .\traces `
  --output .\run
```

The same command works with the standard AWS credential chain, including a
configured profile, temporary credentials, workload role, or instance role.
Use `--aws-profile <name>` when a specific local profile should be selected.
The adapter uses the Bedrock Runtime Converse API so its request shape is
consistent across models that support Converse.

## Shared controls

- `--max-output-tokens` controls the per-call output ceiling for every adapter.
- Provider request failures stop the run instead of being converted into an
  empty JSON object.
- The agreement stage requests JSON output where the provider has a generic
  JSON response control. Its original JSON instructions remain present for
  every provider.
- Provider configuration belongs in the generation request and manifest, not
  in the trace format.

## Adding another provider

Implement the `TextProvider` protocol in
`src/adamast/generation/providers.py`, register it in `create_provider`, and
add a transport contract test. No BASELINE prompt or agreement class should
need to change.

## Provider API references

- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat/create)
- [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages)
- [Google Gemini text generation](https://ai.google.dev/gemini-api/docs/text-generation)
- [AWS Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html)
- [AWS Bedrock API keys](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html)
