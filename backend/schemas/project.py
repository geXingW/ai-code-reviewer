"""Pydantic schemas for GitLab projects."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas._datetime import AwareDatetime
from schemas.project_block_policy import ProjectBlockPolicyCreate, ProjectBlockPolicyRead
from schemas.project_notification_channel import ProjectNotificationChannelRead
from schemas.project_rule import ProjectRuleCreate, ProjectRuleRead

BlockSeverity = Literal["INFO", "WARNING", "BLOCKER"]


class ProjectCreate(BaseModel):
    """Payload for creating a GitLab project configuration."""

    name: str
    gitlab_project_id: str
    gitlab_base_url: str | None = None
    gitlab_access_token: str
    webhook_secret: str
    engine_id: UUID | None = None
    provider_id: UUID | None = None
    enabled: bool = True
    timeout_seconds: int = 300
    max_files: int = 50
    commit_review_enabled: bool = False
    commit_review_max_per_push: int = Field(default=10, ge=1, le=20)
    ignore_paths: list[Any] | None = None
    default_block_severity: BlockSeverity = "BLOCKER"
    deleted_at: datetime | None = None
    # rules 默认 None（未传）——create 时会自动关联所有启用的 BLOCKER 规则，
    # 实现「安全默认」。若前端显式传 [] 则代表 opt out，不做自动关联。
    rules: list[ProjectRuleCreate] | None = None
    block_policies: list[ProjectBlockPolicyCreate] = Field(default_factory=list)

    @field_validator("engine_id", "provider_id", mode="before")
    @classmethod
    def _empty_str_to_none(cls, value: object) -> object:
        """Convert empty string to None for optional UUID fields.

        Frontend form components often submit empty strings for cleared inputs
        instead of omitting the field entirely. Treating '' as None makes the
        API more forgiving without affecting callers that omit the field.
        """
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class ProjectUpdate(BaseModel):
    """Payload for updating a GitLab project configuration."""

    name: str | None = None
    gitlab_project_id: str | None = None
    gitlab_base_url: str | None = None
    gitlab_access_token: str | None = None
    webhook_secret: str | None = None
    engine_id: UUID | None = None
    provider_id: UUID | None = None
    enabled: bool | None = None
    timeout_seconds: int | None = None
    max_files: int | None = None
    commit_review_enabled: bool | None = None
    commit_review_max_per_push: int | None = Field(default=None, ge=1, le=20)
    ignore_paths: list[Any] | None = None
    default_block_severity: BlockSeverity | None = None
    deleted_at: datetime | None = None
    rules: list[ProjectRuleCreate] | None = None
    block_policies: list[ProjectBlockPolicyCreate] | None = None

    @field_validator("engine_id", "provider_id", mode="before")
    @classmethod
    def _empty_str_to_none(cls, value: object) -> object:
        """Convert empty string to None for optional UUID fields."""
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class ProjectRead(BaseModel):
    """GitLab project configuration returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    gitlab_project_id: str
    gitlab_base_url: str
    gitlab_access_token: str
    webhook_secret: str
    engine_id: UUID | None
    provider_id: UUID | None
    enabled: bool
    timeout_seconds: int
    max_files: int
    commit_review_enabled: bool
    commit_review_max_per_push: int
    ignore_paths: list[Any] | None
    default_block_severity: BlockSeverity
    deleted_at: AwareDatetime | None
    rules: list[ProjectRuleRead] = Field(
        default_factory=list,
        validation_alias="project_rules",
        serialization_alias="rules",
    )
    block_policies: list[ProjectBlockPolicyRead] = Field(default_factory=list)
    notification_channels: list[ProjectNotificationChannelRead] = Field(default_factory=list)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("gitlab_access_token", "webhook_secret", mode="before")
    @classmethod
    def mask_secret(cls, value: object) -> str:
        """Mask project secrets in read responses."""

        return "****"
