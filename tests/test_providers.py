from __future__ import annotations

from types import SimpleNamespace

import pytest

from adamast.learning.pipeline import agreement, draft
from adamast.llm.providers import (
    AnthropicProvider,
    BedrockProvider,
    GoogleProvider,
    OpenAIProvider,
    ProviderConfig,
    ProviderConfigurationError,
    ProviderRequestError,
    create_provider,
    resolve_model,
    validate_provider_credentials,
)


class _Recorder:
    def __init__(self, response):
        self.response = response
        self.requests: list[dict] = []

    def record(self, **request):
        self.requests.append(request)
        return self.response


def test_openai_adapter_preserves_system_and_prompt() -> None:
    recorder = _Recorder(
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="  taxonomy json  ")
                )
            ]
        )
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=recorder.record)
        )
    )
    provider = OpenAIProvider(
        ProviderConfig(name="openai", model="model-o"),
        client=client,
    )

    assert provider.complete("USER PROMPT", system="SYSTEM PROMPT") == "taxonomy json"
    assert recorder.requests == [
        {
            "model": "model-o",
            "messages": [
                {"role": "system", "content": "SYSTEM PROMPT"},
                {"role": "user", "content": "USER PROMPT"},
            ],
            "max_completion_tokens": 8192,
            "timeout": 180,
        }
    ]


def test_openai_adapter_maps_json_mode_without_changing_prompt() -> None:
    recorder = _Recorder(
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"ok":true}')
                )
            ]
        )
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=recorder.record)
        )
    )
    provider = OpenAIProvider(
        ProviderConfig(name="openai", model="model-o"),
        client=client,
    )

    assert provider.complete("JSON PROMPT", response_format="json") == '{"ok":true}'
    assert recorder.requests[0]["messages"] == [
        {"role": "user", "content": "JSON PROMPT"}
    ]
    assert recorder.requests[0]["response_format"] == {
        "type": "json_object"
    }


def test_anthropic_adapter_preserves_system_and_prompt() -> None:
    response = SimpleNamespace(
        content=[
            SimpleNamespace(text="first"),
            {"type": "text", "text": "second"},
        ]
    )
    recorder = _Recorder(response)
    client = SimpleNamespace(
        messages=SimpleNamespace(create=recorder.record)
    )
    provider = AnthropicProvider(
        ProviderConfig(name="anthropic", model="model-a"),
        client=client,
    )

    assert provider.complete("USER PROMPT", system="SYSTEM PROMPT") == "first\nsecond"
    assert recorder.requests[0]["messages"] == [
        {"role": "user", "content": "USER PROMPT"}
    ]
    assert recorder.requests[0]["system"] == "SYSTEM PROMPT"


def test_google_adapter_preserves_system_and_prompt() -> None:
    recorder = _Recorder(SimpleNamespace(text="taxonomy json"))
    client = SimpleNamespace(
        models=SimpleNamespace(generate_content=recorder.record)
    )
    provider = GoogleProvider(
        ProviderConfig(name="google", model="model-g"),
        client=client,
    )

    assert provider.complete("USER PROMPT", system="SYSTEM PROMPT") == "taxonomy json"
    assert recorder.requests == [
        {
            "model": "model-g",
            "contents": "USER PROMPT",
            "config": {
                "max_output_tokens": 8192,
                "system_instruction": "SYSTEM PROMPT",
            },
        }
    ]


def test_bedrock_adapter_preserves_system_and_prompt() -> None:
    response = {
        "output": {
            "message": {
                "content": [{"text": "first"}, {"text": "second"}]
            }
        }
    }
    recorder = _Recorder(response)
    client = SimpleNamespace(converse=recorder.record)
    provider = BedrockProvider(
        ProviderConfig(name="bedrock", model="model-b"),
        client=client,
    )

    assert provider.complete("USER PROMPT", system="SYSTEM PROMPT") == "first\nsecond"
    assert recorder.requests == [
        {
            "modelId": "model-b",
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": "USER PROMPT"}],
                }
            ],
            "inferenceConfig": {"maxTokens": 8192},
            "system": [{"text": "SYSTEM PROMPT"}],
        }
    ]


def test_generation_engines_forward_prompt_text_unchanged() -> None:
    calls: list[tuple[str, str, str]] = []

    class CapturingProvider:
        name = "test"
        model = "test-model"

        def complete(
            self,
            prompt: str,
            *,
            system: str = "",
            response_format: str = "text",
        ) -> str:
            calls.append((prompt, system, response_format))
            return "{}"

    provider = CapturingProvider()

    assert draft.call_llm(provider, "DRAFT", "SYSTEM") == "{}"
    assert agreement.call_llm(provider, "AGREEMENT") == "{}"
    assert calls == [
        ("DRAFT", "SYSTEM", "text"),
        ("AGREEMENT", "", "json"),
    ]


def test_empty_provider_response_is_an_explicit_error() -> None:
    recorder = _Recorder(
        SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=""))
            ]
        )
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=recorder.record)
        )
    )
    provider = OpenAIProvider(
        ProviderConfig(name="openai", model="model-o"),
        client=client,
    )

    with pytest.raises(ProviderRequestError, match="returned no text"):
        provider.complete("prompt")


def test_model_resolution_uses_provider_specific_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "configured-claude")
    assert resolve_model("anthropic") == "configured-claude"

    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    with pytest.raises(ProviderConfigurationError, match="BEDROCK_MODEL_ID"):
        resolve_model("bedrock")


def test_api_key_validation_is_provider_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)

    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        validate_provider_credentials("openai")
    with pytest.raises(ProviderConfigurationError, match="ANTHROPIC_API_KEY"):
        validate_provider_credentials("anthropic")
    with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
        validate_provider_credentials("google")

    # Bedrock may use AWS_BEARER_TOKEN_BEDROCK, a profile, an IAM role, or
    # another standard Boto3 credential source, so there is no single key gate.
    validate_provider_credentials("bedrock")


def test_provider_factory_rejects_invalid_shared_limits() -> None:
    with pytest.raises(
        ProviderConfigurationError, match="max output tokens"
    ):
        create_provider(
            "openai",
            "model-o",
            max_output_tokens=0,
            client=object(),
        )
