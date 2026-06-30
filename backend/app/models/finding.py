"""SQLAlchemy model for line-level AI review findings."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.review import Review


class Finding(Base, TimestampMixin):
    """Line-level issue found during an AI review."""

    __tablename__ = "review_findings"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    review_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    line_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    existing_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        server_default=text("0"),
        nullable=False,
    )
    gitlab_discussion_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fp_status: Mapped[str] = mapped_column(
        String(20),
        default="NONE",
        server_default=text("'NONE'"),
        nullable=False,
    )
    fp_marked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fp_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fp_marked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fp_reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fp_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fp_review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    review: Mapped["Review"] = relationship(back_populates="findings", lazy="selectin")
