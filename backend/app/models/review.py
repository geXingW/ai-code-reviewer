"""SQLAlchemy model for AI review records."""

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid, false, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.finding import Finding
    from app.models.project import Project
    from app.models.project_block_policy import ProjectBlockPolicy


class Review(Base, TimestampMixin):
    """A single merge request review execution record."""

    __tablename__ = "reviews"

    # 主键 UUID 由 Python 层生成，不依赖 PG 的 gen_random_uuid()，保证 MySQL 也可用。
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    mr_iid: Mapped[str] = mapped_column(String(255), nullable=False)
    source_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    target_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        server_default=text("'pending'"),
        nullable=False,
    )
    engine_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    policy_applied: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("project_block_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    has_blocker: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    finding_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_llm_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="reviews", lazy="selectin")
    policy: Mapped["ProjectBlockPolicy | None"] = relationship(
        back_populates="reviews",
        lazy="selectin",
    )
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
