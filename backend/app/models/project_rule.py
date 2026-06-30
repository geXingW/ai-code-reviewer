"""SQLAlchemy association model connecting projects and rules."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.rule import Rule


class ProjectRule(Base, TimestampMixin):
    """Per-project rule enablement and severity override."""

    __tablename__ = "project_rules"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rule_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("rules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    severity_override: Mapped[str | None] = mapped_column(String(20), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="project_rules", lazy="selectin")
    rule: Mapped["Rule"] = relationship(back_populates="project_rules", lazy="selectin")
