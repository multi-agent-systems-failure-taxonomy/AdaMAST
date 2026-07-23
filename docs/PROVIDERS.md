# Providers and models

Run AdaMAST generation and judging on the provider you already use. Both
workflows share one provider-neutral text interface, so the prompts and
output validation stay the same when the provider changes.

## 🧩 Supported providers

| Provider flag | Install extra | Credential environment | Model environment |
| --- | --- | --- | --- |
| `openai` | Included with `pip install adamast` | `OPENAI_API_KEY` | `OPENAI_MODEL` |
| `anthropic` | `[anthropic]` | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` |
| `google` | `[google]` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `GEMINI_MODEL` or `GOOGLE_MODEL` |
| `bedrock` | `[bedrock]` | AWS bearer token or normal AWS credential chain | `BEDROCK_MODEL_ID` |

Select a provider explicitly with `--provider` or `ADAMAST_PROVIDER`. Also
pass `--model` or set the provider's model environment variable — only OpenAI
ships a package default model.

## 🟢 OpenAI

```bash
pip install adamast
export OPENAI_API_KEY="..."

adamast generate \
  --provider openai \
  --model gpt-5-nano \
  --traces ./traces.jsonl \
  --output ./taxonomy-run
```

!!! note
    If neither `--model` nor `OPENAI_MODEL` is set, the current package
    defaults to `gpt-5-nano`.

## 🟣 Anthropic

```bash
pip install "adamast[anthropic]"
export ANTHROPIC_API_KEY="..."
export ANTHROPIC_MODEL="YOUR_MODEL_ID"

adamast generate \
  --provider anthropic \
  --traces ./traces.jsonl \
  --output ./taxonomy-run
```

## 🔵 Google

```bash
pip install "adamast[google]"
export GEMINI_API_KEY="..."
export GEMINI_MODEL="YOUR_MODEL_ID"

adamast generate \
  --provider google \
  --traces ./traces.jsonl \
  --output ./taxonomy-run
```

`GOOGLE_API_KEY` and `GOOGLE_MODEL` are accepted aliases.

## 🟠 AWS Bedrock

```bash
pip install "adamast[bedrock]"
export AWS_REGION="us-east-1"
export BEDROCK_MODEL_ID="YOUR_BEDROCK_MODEL_ID"

adamast generate \
  --provider bedrock \
  --traces ./traces.jsonl \
  --output ./taxonomy-run
```

AdaMAST uses the Bedrock Runtime Converse API. Authentication can come from
`AWS_BEARER_TOKEN_BEDROCK` or the normal boto3 chain: environment credentials,
shared configuration, an AWS profile, container credentials, or an instance
role.

Choose a profile and region explicitly when needed:

```bash
adamast generate \
  --provider bedrock \
  --model YOUR_BEDROCK_MODEL_ID \
  --aws-profile research \
  --aws-region us-west-2 \
  --traces ./traces.jsonl \
  --output ./taxonomy-run
```

## 🌱 Environment-only configuration

```bash
export ADAMAST_PROVIDER="anthropic"
export ANTHROPIC_API_KEY="..."
export ANTHROPIC_MODEL="YOUR_MODEL_ID"

adamast generate --traces ./traces.jsonl --output ./taxonomy-run
```

Explicit CLI values take precedence over model environment variables.

## ⏱️ Output and timeout controls

`--max-output-tokens` sets the maximum output for each model call. Its default
is `8192` for both generation and judging.

```bash
adamast judge \
  --provider google \
  --model YOUR_MODEL_ID \
  --max-output-tokens 4096 \
  --taxonomy ./taxonomy-run/taxonomy.json \
  --traces ./new-traces.jsonl
```

!!! note "No silent fallback"
    Provider request errors stop the workflow. AdaMAST does not silently
    switch to another provider or model.

## 🔐 Credential safety

- Put credentials in the provider's environment or standard credential store,
  never in trace files or AdaMAST JSON artifacts.
- Redact secrets from trajectories before generation or judging.
- Use least-privilege AWS credentials that allow only the required Bedrock
  model actions.
- Treat model IDs as experiment inputs and record them in reproducible runs.

## ➡️ Continue with

- [Generate a taxonomy](GENERATION.md) — run generation on the
  provider you just configured.
- [Judge traces](JUDGING.md) — the same provider flags apply to judging.
