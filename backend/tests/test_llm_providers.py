"""Tests for the LLM provider abstraction layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.engines.types import ProviderConfig
from app.llm import (
    AnthropicProvider,
    AuthError,
    ChatMessage,
    ChatResponse,
    CustomProvider,
    LLMProviderConfig,
    OpenAICompatibleProvider,
    RateLimitError,
    ServerError,
    build_provider,
    truncate_to_budget,
)


@dataclass
class _FakeAsyncResponse:
    """Small async response object compatible with httpx.Response usage in providers."""

    status_code: int = 200
    payload: dict[str, Any] | None = None

    def json(self) -> dict[str, Any]:
        """Return JSON payload for provider parsing."""

        return self.payload or {}

    def raise_for_status(self) -> None:
        """Raise an HTTPStatusError for non-success statuses."""

        if self.status_code >= 400:
            request = httpx.Request("POST", "https://llm.example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)


class _FakeHTTPClient:
    """Capture outgoing HTTP calls and return queued responses."""

    def __init__(self, responses: list[_FakeAsyncResponse]) -> None:
        self._responses = responses
        self.requests: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeAsyncResponse:
        """Record request and return next queued response."""

        self.requests.append({"url": url, "headers": headers or {}, "json": json or {}})
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_openai_compatible_chat_normalises_response_and_json_mode() -> None:
    """OpenAI-compatible providers expose one normalized ChatResponse."""

    http_client = _FakeHTTPClient(
        [
            _FakeAsyncResponse(
                payload={
                    "id": "chatcmpl-1",
                    "choices": [{"message": {"content": "{\"ok\": true}"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                }
            )
        ]
    )
    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            provider_id=uuid4(),
            protocol="openai_compatible",
            base_url="https://ark.example.com/api/v3",
            api_key="test-key",
            model="glm-4.5",
            temperature=0.2,
            max_tokens=512,
        ),
        http_client=http_client,
    )

    response = await provider.chat([ChatMessage(role="user", content="ping")])

    assert response == ChatResponse(
        content="{\"ok\": true}",
        model="glm-4.5",
        usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        raw={
            "id": "chatcmpl-1",
            "choices": [{"message": {"content": "{\"ok\": true}"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        },
    )
    request = http_client.requests[0]
    assert request["url"] == "https://ark.example.com/api/v3/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["json"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_anthropic_chat_uses_native_messages_shape() -> None:
    """Anthropic native provider maps Claude responses into ChatResponse."""

    http_client = _FakeHTTPClient(
        [
            _FakeAsyncResponse(
                payload={
                    "id": "msg_1",
                    "content": [{"type": "text", "text": "pong"}],
                    "usage": {"input_tokens": 5, "output_tokens": 2},
                }
            )
        ]
    )
    provider = AnthropicProvider(
        LLMProviderConfig(
            provider_id=uuid4(),
            protocol="anthropic",
            base_url="https://api.anthropic.com/v1",
            api_key="claude-key",
            model="claude-sonnet-4",
            max_tokens=256,
        ),
        http_client=http_client,
    )

    response = await provider.chat([ChatMessage(role="user", content="ping")])

    assert response.content == "pong"
    request = http_client.requests[0]
    assert request["url"] == "https://api.anthropic.com/v1/messages"
    assert request["headers"]["x-api-key"] == "claude-key"
    assert request["headers"]["anthropic-version"] == "2023-06-01"
    assert request["json"]["messages"] == [{"role": "user", "content": "ping"}]


@pytest.mark.asyncio
async def test_custom_provider_uses_auth_template_and_streams_chunks() -> None:
    """Custom provider supports templated auth headers and async streaming."""

    http_client = _FakeHTTPClient(
        [
            _FakeAsyncResponse(payload={"content": "first second", "usage": {"total_tokens": 2}}),
            _FakeAsyncResponse(payload={"content": "first second", "usage": {"total_tokens": 2}}),
        ]
    )
    provider = CustomProvider(
        LLMProviderConfig(
            provider_id=uuid4(),
            protocol="custom",
            base_url="https://custom.example.test/review",
            api_key="custom-key",
            model="custom-reviewer",
            extra={"auth_header_template": "Token {api_key}"},
        ),
        http_client=http_client,
    )

    response = await provider.chat([ChatMessage(role="user", content="ping")])
    chunks = [
        chunk
        async for chunk in provider.stream_chat([ChatMessage(role="user", content="ping")])
    ]

    assert response.content == "first second"
    assert chunks == ["first", "second"]
    request = http_client.requests[0]
    assert request["url"] == "https://custom.example.test/review"
    assert request["headers"]["Authorization"] == "Token custom-key"


def test_factory_builds_provider_from_runtime_config() -> None:
    """Factory accepts ReviewContext ProviderConfig and returns concrete provider."""

    runtime_config = ProviderConfig(
        provider_id=uuid4(),
        provider_type="openai_compatible",
        base_url="https://deepseek.example.test/v1",
        model="deepseek-chat",
        api_key="deepseek-key",
        temperature=0.1,
        max_tokens=1024,
        extra={"default_json_mode": False},
    )

    provider = build_provider(runtime_config)

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.config.model == "deepseek-chat"
    assert provider.config.default_json_mode is False


@pytest.mark.asyncio
async def test_http_errors_are_mapped_to_llm_error_family() -> None:
    """Provider HTTP failures are normalized into stable LLM exceptions."""

    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            provider_id=uuid4(),
            protocol="openai_compatible",
            base_url="https://llm.example.test/v1",
            api_key="bad-key",
            model="model",
        ),
        http_client=_FakeHTTPClient([_FakeAsyncResponse(status_code=429)]),
    )

    with pytest.raises(RateLimitError):
        await provider.chat([ChatMessage(role="user", content="ping")])

    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            provider_id=uuid4(),
            protocol="openai_compatible",
            base_url="https://llm.example.test/v1",
            api_key="bad-key",
            model="model",
        ),
        http_client=_FakeHTTPClient([_FakeAsyncResponse(status_code=401)]),
    )

    with pytest.raises(AuthError):
        await provider.chat([ChatMessage(role="user", content="ping")])

    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            provider_id=uuid4(),
            protocol="openai_compatible",
            base_url="https://llm.example.test/v1",
            api_key="bad-key",
            model="model",
        ),
        http_client=_FakeHTTPClient([_FakeAsyncResponse(status_code=500)]),
        max_retries=0,
    )

    with pytest.raises(ServerError):
        await provider.chat([ChatMessage(role="user", content="ping")])


@pytest.mark.asyncio
async def test_retryable_server_error_retries_then_succeeds() -> None:
    """Transient server errors are retried before returning a normalized response."""

    http_client = _FakeHTTPClient(
        [
            _FakeAsyncResponse(status_code=500),
            _FakeAsyncResponse(
                payload={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"total_tokens": 1},
                }
            ),
        ]
    )
    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            provider_id=uuid4(),
            protocol="openai_compatible",
            base_url="https://llm.example.test/v1",
            api_key="ok-key",
            model="model",
        ),
        http_client=http_client,
    )

    response = await provider.chat([ChatMessage(role="user", content="ping")])

    assert response.content == "ok"
    assert len(http_client.requests) == 2


def test_truncate_to_budget_keeps_recent_messages() -> None:
    """Token budget helper keeps latest messages and trims oversized content."""

    messages = [
        ChatMessage(role="system", content="rules " * 20),
        ChatMessage(role="user", content="old diff " * 20),
        ChatMessage(role="user", content="new diff " * 20),
    ]

    truncated = truncate_to_budget(messages, max_tokens=12)

    assert len(truncated) == 1
    assert truncated[0].content.startswith("new diff")
    assert len(truncated[0].content.split()) <= 12
