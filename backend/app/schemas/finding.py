"""Pydantic schemas for review findings."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

Severity = Literal["INFO", "WARNING", "BLOCKER"]
FalsePositiveStatus = Literal["NONE", "PENDING", "CONFIRMED", "REJECTED"]


class FindingCreate(BaseModel):
    """Payload for creating a review finding."""

    review_id: UUID
    file_path: str
    line_number: int | None = None
    rule_id: str
    severity: Severity
    title: str
    description: str | None = None
    suggestion: str | None = None
    existing_code: str | None = None
    confidence: float = 0.0
    gitlab_discussion_id: str | None = None
    fp_status: FalsePositiveStatus = "NONE"
    fp_marked_by: str | None = None
    fp_marked_at: datetime | None = None
    fp_marked_reason: str | None = None
    fp_reviewed_by: str | None = None
    fp_reviewed_at: datetime | None = None
    fp_review_note: str | None = None


class FindingUpdate(BaseModel):
    """Payload for updating a review finding."""

    review_id: UUID | None = None
    file_path: str | None = None
    line_number: int | None = None
    rule_id: str | None = None
    severity: Severity | None = None
    title: str | None = None
    description: str | None = None
    suggestion: str | None = None
    existing_code: str | None = None
    confidence: float | None = None
    gitlab_discussion_id: str | None = None
    fp_status: FalsePositiveStatus | None = None
    fp_marked_by: str | None = None
    fp_marked_at: datetime | None = None
    fp_marked_reason: str | None = None
    fp_reviewed_by: str | None = None
    fp_reviewed_at: datetime | None = None
    fp_review_note: str | None = None


class FindingRead(BaseModel):
    """Review finding returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_id: UUID
    file_path: str
    line_number: int | None
    rule_id: str
    severity: Severity
    title: str
    description: str | None
    suggestion: str | None
    existing_code: str | None
    confidence: float
    gitlab_discussion_id: str | None
    fp_status: FalsePositiveStatus
    fp_marked_by: str | None
    fp_marked_at: datetime | None
    fp_marked_reason: str | None
    fp_reviewed_by: str | None
    fp_reviewed_at: datetime | None
    fp_review_note: str | None
    created_at: datetime
    updated_at: datetime
