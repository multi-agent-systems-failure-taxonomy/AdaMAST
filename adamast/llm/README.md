# adamast/llm/

Model routing and provider transports. Everything that talks to a model API
for learning goes through here; the interactive hosts' native subagent path
does not need this package at all.

## Programs

| File | Purpose |
|---|---|
| [`providers.py`](providers.py) | Provider-neutral transports (OpenAI-compatible, Anthropic, Bedrock) behind one call shape |
| [`models.py`](models.py) | Model naming, profiles, and routing |
| [`learning_calls.py`](learning_calls.py) | Generation, refinement, and support-judge model calls with caps and retries |
| [`reflection_judge_llm.py`](reflection_judge_llm.py) | LLM plumbing for the Reflection Judge |

Provider prompt assets live in [`assets/`](assets/).
