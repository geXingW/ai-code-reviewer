"""Factory helpers for constructing concrete LLM providers."""

from __future__ import annotations

from typing import Any, Protocol, cast
from uuid import UUID

from app.engines.types import ProviderConfig
from app.llm.base import (
    AnthropicProvider,
    AsyncHTTPClient,
    CustomProvider,
    LLMProvider,
    LLMProviderConfig,
    OpenAICompatibleProvider,
    ProviderProtocol,
)


class DBProviderLike(Protocol):
    """Structural protocol for the SQLAlchemy Provider model."""

    id: UUID
    protocol: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    extra_headers: dict[str, Any] | None


def build_provider(
    provider: ProviderConfig | DBProviderLike | LLMProviderConfig,
    *,
    http_client: AsyncHTTPClient | None = None,
) -> LLMProvider:
    """Build a concrete provider from runtime config or a DB Provider row.

    Args:
        provider: Runtime ``ProviderConfig``, DB model-like object, or already-normalized
            ``LLMProviderConfig``.
        http_client: Optional async HTTP client for tests or custom transports.

    Returns:
        Concrete provider adapter matching the configured protocol.

    Raises:
        ValueError: If provider protocol is unsupported.
    """

    config = _normalise_config(provider)
    if config.protocol == "openai_compatible":
        return OpenAICompatibleProvider(config, http_client=http_client)
    if config.protocol == "anthropic":
        return AnthropicProvider(config, http_client=http_client)
    return CustomProvider(config, http_client=http_client)


def _normalise_config(
    provider: ProviderConfig | DBProviderLike | LLMProviderConfig,
) -> LLMProviderConfig:
    """Convert supported provider config shapes into ``LLMProviderConfig``."""

    if isinstance(provider, LLMProviderConfig):
        return provider

    if isinstance(provider, ProviderConfig):
        protocol = _normalise_protocol(provider.provider_type)
        return LLMProviderConfig(
            provider_id=provider.provider_id,
            protocol=protocol,
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=provider.model,
            temperature=provider.temperature,
            max_tokens=provider.max_tokens,
            default_json_mode=bool(provider.extra.get("default_json_mode", True)),
            extra_headers=_coerce_headers(provider.extra.get("extra_headers")),
            extra=provider.extra,
        )

    db_provider = provider
    extra_headers = db_provider.extra_headers or {}
    return LLMProviderConfig(
        provider_id=db_provider.id,
        protocol=_normalise_protocol(str(db_provider.protocol)),
        base_url=str(db_provider.base_url),
        api_key=str(db_provider.api_key),
        model=str(db_provider.model),
        temperature=float(db_provider.temperature),
        max_tokens=int(db_provider.max_tokens),
        extra_headers=_coerce_headers(extra_headers),
        extra={"extra_headers": extra_headers},
    )


def _normalise_protocol(value: str) -> ProviderProtocol:
    aliases = {
        "openai-compatible": "openai_compatible",
        "openai_compat": "openai_compatible",
        "openai": "openai_compatible",
        "anthropic_native": "anthropic",
    }
    normalized = aliases.get(value, value)
    if normalized not in {"openai_compatible", "anthropic", "custom"}:
        msg = f"unsupported LLM provider protocol: {value}"
        raise ValueError(msg)
    return cast(ProviderProtocol, normalized)


def _coerce_headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    headers: dict[str, str] = {}
    for key, raw in value.items():
        if isinstance(key, str) and isinstance(raw, str):
            headers[key] = raw
    return headers
