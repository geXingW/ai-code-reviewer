"""Pydantic schemas for AI review records."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas._datetime import AwareDatetime

ReviewStatus = Literal["pending", "running", "done", "failed"]


class ReviewCreate(BaseModel):
    """Payload for creating a review record."""

    project_id: UUID
    mr_iid: str
    source_branch: str
    target_branch: str
    commit_sha: str
    status: ReviewStatus = "pending"
    engine_used: str | None = None
    provider_used: str | None = None
    policy_applied: UUID | None = None
    has_blocker: bool = False
    finding_count: int = 0
    duration_ms: int | None = None
    raw_llm_output: str | None = None


class ReviewUpdate(BaseModel):
    """Payload for updating a review record."""

    project_id: UUID | None = None
    mr_iid: str | None = None
    source_branch: str | None = None
    target_branch: str | None = None
    commit_sha: str | None = None
    status: ReviewStatus | None = None
    engine_used: str | None = None
    provider_used: str | None = None
    policy_applied: UUID | None = None
    has_blocker: bool | None = None
    finding_count: int | None = None
    duration_ms: int | None = None
    raw_llm_output: str | None = None


class ReviewRead(BaseModel):
    """Review record returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    mr_iid: str
    source_branch: str
    target_branch: str
    commit_sha: str
    status: ReviewStatus
    engine_used: str | None
    provider_used: str | None
    policy_applied: UUID | None
    has_blocker: bool
    finding_count: int
    duration_ms: int | None
    raw_llm_output: str | None
    # 展示用冗余字段：project_name 来自关联 Project.name，rules_used 为 findings 的
    # rule_id 去重列表。由 admin API 层在返回前填充，不直接映射 ORM 列。
    project_name: str | None = None
    rules_used: list[str] = Field(default_factory=list)
    created_at: AwareDatetime
    updated_at: AwareDatetime
