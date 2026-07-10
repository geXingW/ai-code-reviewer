"""SQLAlchemy model for approved false-positive negative examples."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.project import Project


class NegativeExample(Base, TimestampMixin):
    """Approved false-positive example used to improve future prompts."""

    __tablename__ = "negative_examples"

    # 主键 UUID 由 Python 层生成，不依赖 PG 的 gen_random_uuid()，保证 MySQL 也可用。
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    code_snippet: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_finding_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("review_findings.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project | None"] = relationship(
        back_populates="negative_examples",
        lazy="selectin",
    )
