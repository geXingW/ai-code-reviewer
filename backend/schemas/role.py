"""Pydantic schemas for RBAC roles."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RoleCreate(BaseModel):
    """Payload for creating a new role."""
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=256)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """Payload for updating an existing role."""
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=256)
    permissions: list[str] | None = None


class RoleRead(BaseModel):
    """Response for a role record."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    is_system: bool
    permissions: list[str] = []
    user_count: int = 0
    created_at: datetime
    updated_at: datetime