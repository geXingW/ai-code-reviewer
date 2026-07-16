"""Pydantic schemas for audit logs."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas._datetime import AwareDatetime


class AuditLogCreate(BaseModel):
    """Payload for creating an audit log entry."""

    actor: str | None = None
    action: str
    resource_type: str
    resource_id: UUID | None = None
    details: dict[str, Any] | None = None


class AuditLogUpdate(BaseModel):
    """Payload for updating an audit log entry."""

    actor: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: UUID | None = None
    details: dict[str, Any] | None = None


class AuditLogRead(BaseModel):
    """Audit log entry returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor: str | None
    action: str
    resource_type: str
    resource_id: UUID | None
    details: dict[str, Any] | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
