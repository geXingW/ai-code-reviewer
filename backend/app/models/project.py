"""SQLAlchemy model for GitLab projects."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.encryption import EncryptedString

if TYPE_CHECKING:
    from app.models.engine import Engine
    from app.models.negative_example import NegativeExample
    from app.models.project_block_policy import ProjectBlockPolicy
    from app.models.project_rule import ProjectRule
    from app.models.provider import Provider
    from app.models.review import Review


class Project(Base, TimestampMixin):
    """GitLab project configuration for AI code review."""

    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    gitlab_project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    gitlab_access_token: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    webhook_secret: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    engine_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("engines.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=300,
        server_default=text("300"),
        nullable=False,
    )
    max_files: Mapped[int] = mapped_column(
        Integer,
        default=50,
        server_default=text("50"),
        nullable=False,
    )
    ignore_paths: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    default_block_severity: Mapped[str] = mapped_column(
        String(30),
        default="BLOCKER",
        server_default=text("'BLOCKER'"),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    engine: Mapped["Engine | None"] = relationship(back_populates="projects", lazy="selectin")
    provider: Mapped["Provider | None"] = relationship(back_populates="projects", lazy="selectin")
    project_rules: Mapped[list["ProjectRule"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    block_policies: Mapped[list["ProjectBlockPolicy"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    negative_examples: Mapped[list["NegativeExample"]] = relationship(
        back_populates="project",
        lazy="selectin",
    )
