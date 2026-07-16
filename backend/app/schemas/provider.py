"""Pydantic schemas for LLM providers."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas._datetime import AwareDatetime

ProviderProtocol = Literal["openai_compatible", "anthropic", "custom"]


class ProviderCreate(BaseModel):
    """Payload for creating an LLM provider."""

    name: str
    protocol: ProviderProtocol
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 4096
    extra_headers: dict[str, Any] | None = None
    enabled: bool = True


class ProviderUpdate(BaseModel):
    """Payload for updating an LLM provider."""

    name: str | None = None
    protocol: ProviderProtocol | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra_headers: dict[str, Any] | None = None
    enabled: bool | None = None


class ProviderRead(BaseModel):
    """LLM provider returned by API responses with sensitive fields masked."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    protocol: ProviderProtocol
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    extra_headers: dict[str, Any] | None
    enabled: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("api_key", mode="before")
    @classmethod
    def mask_api_key(cls, value: object) -> str:
        """Mask provider API keys in read responses."""

        return "****"
