"""SQLAlchemy model for branch-based project block policies."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.review import Review


class ProjectBlockPolicy(Base, TimestampMixin):
    """Branch matching policy that controls review blocking behavior."""

    __tablename__ = "project_block_policies"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    branch_pattern: Mapped[str] = mapped_column(String(255), nullable=False)
    block_severity: Mapped[str] = mapped_column(String(30), nullable=False)
    block_on_engine_error: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    require_all_resolved: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False)

    project: Mapped["Project | None"] = relationship(
        back_populates="block_policies",
        lazy="selectin",
    )
    reviews: Mapped[list["Review"]] = relationship(back_populates="policy", lazy="selectin")
