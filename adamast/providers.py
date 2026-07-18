"""Provider-neutral text generation for AdaMAST taxonomy strategies."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Literal, Mapping, Protocol, runtime_checkable


SUPPORTED_PROVIDERS = ("openai", "anthropic", "google", "bedrock")
DEFAULT_OPENAI_MODEL = "gpt-5-nano"
DEFAULT_MAX_OUTPUT_TOKENS = 8192


class ProviderConfigurationError(ValueError):
    """Raised when a provider cannot be configured safely."""


class ProviderRequestError(RuntimeError):
    """Raised when a provider request fails or has no text response."""


@dataclass(frozen=True)
class ProviderConfig:
    """Transport-specific settings kept outside generation strategies."""

    name: str
    model: str
    timeout: int = 180
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    aws_region: str | None = None
    aws_profile: str | None = None


@runtime_checkable
class TextProvider(Protocol):
    """Minimal model contract consumed by every taxonomy-generation stage."""

    name: str
    model: str

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        response_format: Literal["text", "json"] = "text",
    ) -> str:
        """Return plain response text for unchanged AdaMAST prompt content."""


def normalize_provider_name(name: str | None) -> str:
    if not name:
        raise ProviderConfigurationError(
            "taxonomy generation requires --provider or ADAMAST_PROVIDER"
        )
    normalized = name.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise ProviderConfigurationError(
            f"unsupported provider {name!r}; choose one of: {supported}"
        )
    return normalized


def resolve_model(provider: str, explicit: str | None = None) -> str:
    """Resolve a model without hard-coding volatile non-OpenAI model names."""

    name = normalize_provider_name(provider)
    if explicit and explicit.strip():
        return explicit.strip()

    environment_names = {
        "openai": ("OPENAI_MODEL",),
        "anthropic": ("ANTHROPIC_MODEL",),
        "google": ("GEMINI_MODEL", "GOOGLE_MODEL"),
        "bedrock": ("BEDROCK_MODEL_ID",),
    }
    for variable in environment_names[name]:
        value = os.getenv(variable)
        if value and value.strip():
            return value.strip()

    if name == "openai":
        return DEFAULT_OPENAI_MODEL
    variables = " or ".join(environment_names[name])
    raise ProviderConfigurationError(
        f"{name} requires --model or the {variables} environment variable"
    )


def validate_provider_credentials(provider: str) -> None:
    """Fail early for API-key providers without reading or printing secrets."""

    name = normalize_provider_name(provider)
    required = {
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    }
    variables = required.get(name)
    if variables and not any(
        value and value.strip()
        for variable in variables
        if (value := os.getenv(variable)) is not None
    ):
        joined = " or ".join(variables)
        raise ProviderConfigurationError(f"{name} requires {joined}")
    # Bedrock intentionally has no single credential check. Boto3 supports the
    # Bedrock bearer token as well as its normal AWS credential/provider chain.


def create_provider(
    provider: str,
    model: str,
    *,
    timeout: int = 180,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    aws_region: str | None = None,
    aws_profile: str | None = None,
    client: Any = None,
) -> TextProvider:
    """Create one provider adapter, optionally around an injected SDK client."""

    if not model or not model.strip():
        raise ProviderConfigurationError("provider model ID cannot be empty")
    if timeout <= 0:
        raise ProviderConfigurationError("provider timeout must be positive")
    if max_output_tokens <= 0:
        raise ProviderConfigurationError(
            "max output tokens must be positive"
        )
    config = ProviderConfig(
        name=normalize_provider_name(provider),
        model=model.strip(),
        timeout=timeout,
        max_output_tokens=max_output_tokens,
        aws_region=aws_region,
        aws_profile=aws_profile,
    )
    adapters = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "google": GoogleProvider,
        "bedrock": BedrockProvider,
    }
    try:
        return adapters[config.name](config, client=client)
    except ProviderConfigurationError:
        raise
    except Exception as exc:
        raise ProviderConfigurationError(
            f"could not initialize {config.name}: {exc}"
        ) from exc


class _BaseProvider:
    name: str

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.name = config.name
        self.model = config.model

    def _request_failed(self, exc: Exception) -> ProviderRequestError:
        return ProviderRequestError(f"{self.name} request failed: {exc}")

    def _require_text(self, text: Any) -> str:
        value = str(text or "").strip()
        if not value:
            raise ProviderRequestError(f"{self.name} returned no text")
        return value


class OpenAIProvider(_BaseProvider):
    """OpenAI Chat Completions adapter preserving BASELINE behavior."""

    name = "openai"

    def __init__(self, config: ProviderConfig, *, client: Any = None) -> None:
        super().__init__(config)
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise _missing_dependency("openai", "openai") from exc
            client = OpenAI()
        self.client = client

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        response_format: Literal["text", "json"] = "text",
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": self.config.max_output_tokens,
            "timeout": self.config.timeout,
        }
        if response_format == "json":
            request["response_format"] = {"type": "json_object"}
        try:
            response = self.client.chat.completions.create(**request)
            return self._require_text(response.choices[0].message.content)
        except ProviderRequestError:
            raise
        except Exception as exc:
            raise self._request_failed(exc) from exc


class AnthropicProvider(_BaseProvider):
    """Anthropic Messages API adapter."""

    name = "anthropic"

    def __init__(self, config: ProviderConfig, *, client: Any = None) -> None:
        super().__init__(config)
        if client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise _missing_dependency("anthropic", "anthropic") from exc
            client = Anthropic()
        self.client = client

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        response_format: Literal["text", "json"] = "text",
    ) -> str:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.config.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": self.config.timeout,
        }
        if system:
            request["system"] = system
        try:
            response = self.client.messages.create(**request)
            text = "\n".join(
                str(_field(block, "text"))
                for block in response.content
                if _field(block, "text")
            )
            return self._require_text(text)
        except ProviderRequestError:
            raise
        except Exception as exc:
            raise self._request_failed(exc) from exc


class GoogleProvider(_BaseProvider):
    """Google Gen AI SDK adapter."""

    name = "google"

    def __init__(self, config: ProviderConfig, *, client: Any = None) -> None:
        super().__init__(config)
        if client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise _missing_dependency("google-genai", "google") from exc
            client = genai.Client()
        self.client = client

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        response_format: Literal["text", "json"] = "text",
    ) -> str:
        generation_config: dict[str, Any] = {
            "max_output_tokens": self.config.max_output_tokens
        }
        if system:
            generation_config["system_instruction"] = system
        if response_format == "json":
            generation_config["response_mime_type"] = "application/json"
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=generation_config,
            )
            return self._require_text(response.text)
        except ProviderRequestError:
            raise
        except Exception as exc:
            raise self._request_failed(exc) from exc


class BedrockProvider(_BaseProvider):
    """AWS Bedrock Runtime Converse API adapter."""

    name = "bedrock"

    def __init__(self, config: ProviderConfig, *, client: Any = None) -> None:
        super().__init__(config)
        if client is None:
            try:
                import boto3
                from botocore.config import Config as BotoConfig
            except ImportError as exc:
                raise _missing_dependency("boto3", "bedrock") from exc

            session_options: dict[str, Any] = {}
            if config.aws_profile:
                session_options["profile_name"] = config.aws_profile
            session = boto3.Session(**session_options)
            client_options: dict[str, Any] = {
                "config": BotoConfig(
                    connect_timeout=config.timeout,
                    read_timeout=config.timeout,
                    retries={"mode": "standard", "max_attempts": 3},
                )
            }
            if config.aws_region:
                client_options["region_name"] = config.aws_region
            client = session.client("bedrock-runtime", **client_options)
        self.client = client

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        response_format: Literal["text", "json"] = "text",
    ) -> str:
        request: dict[str, Any] = {
            "modelId": self.model,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {
                "maxTokens": self.config.max_output_tokens,
            },
        }
        if system:
            request["system"] = [{"text": system}]
        try:
            response = self.client.converse(**request)
            content = response["output"]["message"]["content"]
            text = "\n".join(
                str(block["text"])
                for block in content
                if isinstance(block, Mapping) and block.get("text")
            )
            return self._require_text(text)
        except ProviderRequestError:
            raise
        except Exception as exc:
            raise self._request_failed(exc) from exc


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _missing_dependency(package: str, extra: str) -> ProviderConfigurationError:
    return ProviderConfigurationError(
        f"{package} is not installed; install AdaMAST with the [{extra}] extra"
    )
