"""Pydantic schemas for review engines."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EngineCreate(BaseModel):
    """Payload for creating a review engine."""

    name: str
    engine_type: str
    config: dict[str, Any] | None = None
    enabled: bool = True


class EngineUpdate(BaseModel):
    """Payload for updating a review engine."""

    name: str | None = None
    engine_type: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class EngineRead(BaseModel):
    """Review engine returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    engine_type: str
    config: dict[str, Any] | None
    enabled: bool
    created_at: datetime
    updated_at: datetime
