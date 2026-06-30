"""Pydantic schemas for project-rule associations."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

Severity = Literal["INFO", "WARNING", "BLOCKER"]


class ProjectRuleCreate(BaseModel):
    """Payload for enabling a rule on a project."""

    project_id: UUID
    rule_id: UUID
    enabled: bool = True
    severity_override: Severity | None = None


class ProjectRuleUpdate(BaseModel):
    """Payload for updating a project-rule association."""

    enabled: bool | None = None
    severity_override: Severity | None = None


class ProjectRuleRead(BaseModel):
    """Project-rule association returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    project_id: UUID
    rule_id: UUID
    enabled: bool
    severity_override: Severity | None
    created_at: datetime
    updated_at: datetime
