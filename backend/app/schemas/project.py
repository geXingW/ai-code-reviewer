"""Pydantic schemas for GitLab projects."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

BlockSeverity = Literal["INFO", "WARNING", "BLOCKER"]


class ProjectCreate(BaseModel):
    """Payload for creating a GitLab project configuration."""

    name: str
    gitlab_project_id: str
    gitlab_access_token: str
    webhook_secret: str
    engine_id: UUID | None = None
    provider_id: UUID | None = None
    enabled: bool = True
    timeout_seconds: int = 300
    max_files: int = 50
    ignore_paths: list[Any] | None = None
    default_block_severity: BlockSeverity = "BLOCKER"
    deleted_at: datetime | None = None


class ProjectUpdate(BaseModel):
    """Payload for updating a GitLab project configuration."""

    name: str | None = None
    gitlab_project_id: str | None = None
    gitlab_access_token: str | None = None
    webhook_secret: str | None = None
    engine_id: UUID | None = None
    provider_id: UUID | None = None
    enabled: bool | None = None
    timeout_seconds: int | None = None
    max_files: int | None = None
    ignore_paths: list[Any] | None = None
    default_block_severity: BlockSeverity | None = None
    deleted_at: datetime | None = None


class ProjectRead(BaseModel):
    """GitLab project returned by API responses with sensitive fields masked."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    gitlab_project_id: str
    gitlab_access_token: str
    webhook_secret: str
    engine_id: UUID | None
    provider_id: UUID | None
    enabled: bool
    timeout_seconds: int
    max_files: int
    ignore_paths: list[Any] | None
    default_block_severity: BlockSeverity
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("gitlab_access_token", "webhook_secret", mode="before")
    @classmethod
    def mask_secret(cls, value: object) -> str:
        """Mask project secrets in read responses."""

        return "****"
