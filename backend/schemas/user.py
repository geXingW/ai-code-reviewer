"""Pydantic schemas for RBAC users."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    """Payload for creating a new user."""
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    role_id: UUID | None = None
    enabled: bool = True


class UserUpdate(BaseModel):
    """Payload for updating an existing user (all fields optional)."""
    username: str | None = Field(default=None, min_length=1, max_length=64)
    password: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    role_id: UUID | None = None
    enabled: bool | None = None


class UserRead(BaseModel):
    """Response for a user record."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str | None
    role_id: UUID | None
    role_name: str | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    # 由 enrich 填充
    project_ids: list[UUID] = []
    permissions: list[str] = []


class UserLoginResponse(BaseModel):
    """Response for login (reuses existing LoginResponse shape)."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str
    display_name: str | None = None
    permissions: list[str] = []
    project_ids: list[UUID] = []


class UserProjectAssignRequest(BaseModel):
    """Payload for assigning projects to a user."""
    project_ids: list[UUID] = Field(default_factory=list)