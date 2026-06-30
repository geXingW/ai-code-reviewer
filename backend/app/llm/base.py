"""LLM provider contracts, common models, and concrete HTTP adapters."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal, Protocol, cast
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

ProviderProtocol = Literal["openai_compatible", "anthropic", "custom"]
ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    """Provider-neutral chat message."""

    model_config = ConfigDict(extra="forbid")

    role: ChatRole
    content: str


class ChatResponse(BaseModel):
    """Provider-neutral chat response."""

    model_config = ConfigDict(extra="forbid")

    content: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class LLMProviderConfig(BaseModel):
    """Resolved provider settings used to instantiate an LLM adapter."""

    model_config = ConfigDict(extra="forbid")

    provider_id: UUID
    protocol: ProviderProtocol
    base_url: str
    api_key: str = Field(repr=False)
    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    default_json_mode: bool = True
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class LLMError(Exception):
    """Base exception for all normalized LLM provider failures."""


class RateLimitError(LLMError):
    """Raised when upstream returns a rate-limit response."""


class AuthError(LLMError):
    """Raised when provider credentials are rejected."""


class TimeoutError(LLMError):
    """Raised when a provider request times out."""


class ServerError(LLMError):
    """Raised when upstream returns a retryable server failure."""


class ProviderResponseError(LLMError):
    """Raised when a provider response shape cannot be parsed."""


class HTTPResponseLike(Protocol):
    """Minimal response protocol returned by async HTTP clients."""

    def json(self) -> object:
        """Return decoded JSON payload."""

    def raise_for_status(self) -> None:
        """Raise for non-successful HTTP responses."""


class AsyncHTTPClient(Protocol):
    """Minimal async HTTP client protocol used by providers."""

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
    ) -> HTTPResponseLike:
        """Send an HTTP POST request."""


class _HttpxAsyncClientAdapter:
    """Small adapter that keeps provider tests independent from httpx internals."""

    def __init__(self, *, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            return await client.post(url, headers=headers, json=json)


class LLMProvider(ABC):
    """Abstract provider contract used by review engines."""

    def __init__(
        self,
        config: LLMProviderConfig,
        *,
        http_client: AsyncHTTPClient | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        """Create an LLM provider instance."""

        if not config.base_url.strip():
            msg = "provider base_url must not be empty"
            raise ValueError(msg)
        if not config.api_key.strip():
            msg = "provider api_key must not be empty"
            raise ValueError(msg)
        if not config.model.strip():
            msg = "provider model must not be empty"
            raise ValueError(msg)
        if timeout_seconds <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)
        if max_retries < 0:
            msg = "max_retries must not be negative"
            raise ValueError(msg)

        self.config = config
        self._http_client = http_client or _HttpxAsyncClientAdapter(timeout_seconds=timeout_seconds)
        self._max_retries = max_retries

    @abstractmethod
    async def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        """Return one complete chat response."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Reserved embedding contract for later semantic matching."""

        _ = texts
        msg = "embed() is reserved and not implemented yet"
        raise NotImplementedError(msg)

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        """Default streaming fallback splits a non-streaming response into chunks."""

        response = await self.chat(messages)
        for chunk in response.content.split():
            yield chunk

    async def _post_with_retry(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> HTTPResponseLike:
        """POST JSON with bounded exponential retry for transient failures."""

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http_client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return cast(HTTPResponseLike, response)
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise TimeoutError("LLM provider request timed out") from exc
            except httpx.HTTPStatusError as exc:
                mapped = _map_http_status(exc.response.status_code, exc)
                if isinstance(mapped, ServerError) and attempt < self._max_retries:
                    logger.warning("LLM provider server error; retrying attempt %s", attempt + 1)
                else:
                    raise mapped from exc
            await asyncio.sleep(min(0.1 * (2**attempt), 1.0))
        raise ServerError("LLM provider request failed after retries")


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible chat-completions provider adapter."""

    async def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        """Call ``/chat/completions`` and normalize the response."""

        payload: dict[str, object] = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [message.model_dump() for message in messages],
        }
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens
        if self.config.default_json_mode:
            payload["response_format"] = {"type": "json_object"}

        response = await self._post_with_retry(
            self.config.base_url.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                **self.config.extra_headers,
            },
            payload=payload,
        )
        data = _ensure_mapping(response.json())
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            msg = "OpenAI-compatible response missing choices[0].message.content"
            raise ProviderResponseError(msg) from exc
        if not isinstance(content, str):
            msg = "OpenAI-compatible response content must be a string"
            raise ProviderResponseError(msg)
        return ChatResponse(
            content=content,
            model=self.config.model,
            usage=_normalise_usage(data.get("usage")),
            raw=dict(data),
        )


class AnthropicProvider(LLMProvider):
    """Anthropic native messages provider adapter."""

    async def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        """Call Anthropic ``/messages`` and normalize the response."""

        system_messages = [message.content for message in messages if message.role == "system"]
        non_system_messages = [message for message in messages if message.role != "system"]
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [message.model_dump() for message in non_system_messages],
            "max_tokens": self.config.max_tokens or 4096,
            "temperature": self.config.temperature,
        }
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)

        response = await self._post_with_retry(
            self.config.base_url.rstrip("/") + "/messages",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": str(self.config.extra.get("anthropic_version", "2023-06-01")),
                **self.config.extra_headers,
            },
            payload=payload,
        )
        data = _ensure_mapping(response.json())
        content = _extract_anthropic_text(data.get("content"))
        return ChatResponse(
            content=content,
            model=self.config.model,
            usage=_normalise_usage(data.get("usage")),
            raw=dict(data),
        )


class CustomProvider(LLMProvider):
    """Custom HTTP JSON provider adapter with templated Authorization header."""

    async def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        """POST provider-neutral JSON to a custom endpoint."""

        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": self.config.temperature,
        }
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens

        response = await self._post_with_retry(
            self.config.base_url.rstrip("/"),
            headers={
                "Authorization": self._render_auth_header(),
                **self.config.extra_headers,
            },
            payload=payload,
        )
        data = _ensure_mapping(response.json())
        content = data.get("content")
        if not isinstance(content, str):
            msg = "Custom provider response must include string field 'content'"
            raise ProviderResponseError(msg)
        return ChatResponse(
            content=content,
            model=self.config.model,
            usage=_normalise_usage(data.get("usage")),
            raw=dict(data),
        )

    def _render_auth_header(self) -> str:
        template = str(self.config.extra.get("auth_header_template", "Bearer {api_key}"))
        return template.replace("{api_key}", self.config.api_key)


def count_tokens(text: str) -> int:
    """Return an approximate token count with a tiktoken-compatible fallback.

    The production deployment may install ``tiktoken`` for model-specific counts;
    tests and minimal installs fall back to whitespace tokenization.
    """

    try:
        import tiktoken  # type: ignore[import-not-found]

        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:  # noqa: BLE001 - optional dependency fallback by design.
        return len(text.split())


def truncate_to_budget(messages: Sequence[ChatMessage], *, max_tokens: int) -> list[ChatMessage]:
    """Keep the most recent messages whose content fits within ``max_tokens``."""

    if max_tokens <= 0:
        msg = "max_tokens must be positive"
        raise ValueError(msg)

    kept_reversed: list[ChatMessage] = []
    remaining = max_tokens
    for message in reversed(messages):
        tokens = count_tokens(message.content)
        if tokens <= remaining:
            kept_reversed.append(message)
            remaining -= tokens
            continue
        if not kept_reversed:
            words = message.content.split()
            truncated = " ".join(words[:max_tokens])
            kept_reversed.append(message.model_copy(update={"content": truncated}))
        break
    return list(reversed(kept_reversed))


def _map_http_status(status_code: int, exc: Exception) -> LLMError:
    if status_code == 429:
        return RateLimitError("LLM provider rate limit exceeded")
    if status_code in {401, 403}:
        return AuthError("LLM provider authentication failed")
    if status_code >= 500:
        return ServerError("LLM provider server error")
    return LLMError(f"LLM provider HTTP error: {status_code}")


def _ensure_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = "LLM provider response must be a JSON object"
        raise ProviderResponseError(msg)
    return cast(dict[str, Any], value)


def _normalise_usage(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    usage: dict[str, int] = {}
    for key, raw in value.items():
        if isinstance(key, str) and isinstance(raw, int):
            usage[key] = raw
    return usage


def _extract_anthropic_text(value: object) -> str:
    if not isinstance(value, list):
        msg = "Anthropic response content must be a list"
        raise ProviderResponseError(msg)
    chunks: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if item.get("type") == "text" and isinstance(text, str):
            chunks.append(text)
    if not chunks:
        msg = "Anthropic response did not contain text content"
        raise ProviderResponseError(msg)
    return "".join(chunks)
