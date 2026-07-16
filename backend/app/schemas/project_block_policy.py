"""Pydantic schemas for project block policies."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas._datetime import AwareDatetime

BlockPolicySeverity = Literal["NONE", "INFO", "WARNING", "BLOCKER", "ENGINE_ERROR_ONLY"]


class ProjectBlockPolicyCreate(BaseModel):
    """Payload for creating a branch block policy."""

    project_id: UUID | None = None
    branch_pattern: str
    block_severity: BlockPolicySeverity
    block_on_engine_error: bool = False
    require_all_resolved: bool = False
    priority: int


class ProjectBlockPolicyUpdate(BaseModel):
    """Payload for updating a branch block policy."""

    project_id: UUID | None = None
    branch_pattern: str | None = None
    block_severity: BlockPolicySeverity | None = None
    block_on_engine_error: bool | None = None
    require_all_resolved: bool | None = None
    priority: int | None = None


class ProjectBlockPolicyRead(BaseModel):
    """Branch block policy returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    branch_pattern: str
    block_severity: BlockPolicySeverity
    block_on_engine_error: bool
    require_all_resolved: bool
    priority: int
    created_at: AwareDatetime
    updated_at: AwareDatetime
