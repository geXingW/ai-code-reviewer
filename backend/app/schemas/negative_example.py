"""Pydantic schemas for false-positive negative examples."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas._datetime import AwareDatetime


class NegativeExampleCreate(BaseModel):
    """Payload for creating an approved negative example."""

    rule_id: str
    project_id: UUID | None = None
    code_snippet: str
    explanation: str | None = None
    source_finding_id: UUID | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None


class NegativeExampleUpdate(BaseModel):
    """Payload for updating an approved negative example."""

    rule_id: str | None = None
    project_id: UUID | None = None
    code_snippet: str | None = None
    explanation: str | None = None
    source_finding_id: UUID | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None


class NegativeExampleRead(BaseModel):
    """Negative example returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_id: str
    project_id: UUID | None
    code_snippet: str
    explanation: str | None
    source_finding_id: UUID | None
    approved_by: str | None
    approved_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
