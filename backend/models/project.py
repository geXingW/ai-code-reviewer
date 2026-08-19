"""SQLAlchemy model for GitLab projects."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Uuid, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base, TimestampMixin
from models.encryption import EncryptedString

if TYPE_CHECKING:
    from models.engine import Engine
    from models.negative_example import NegativeExample
    from models.project_block_policy import ProjectBlockPolicy
    from models.project_negative_prompt import ProjectNegativePrompt
    from models.project_notification_channel import ProjectNotificationChannel
    from models.project_rule import ProjectRule
    from models.provider import Provider
    from models.review import Review
    from models.user_mapping import UserMapping


class Project(Base, TimestampMixin):
    """GitLab project configuration for AI code review."""

    __tablename__ = "projects"

    # 主键 UUID 由 Python 层生成，不依赖 PG 的 gen_random_uuid()，保证 MySQL 也可用。
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    gitlab_project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    gitlab_base_url: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")
    gitlab_access_token: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    webhook_secret: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    engine_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("engines.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=300,
        nullable=False,
    )
    max_files: Mapped[int] = mapped_column(
        Integer,
        default=50,
        nullable=False,
    )
    ignore_paths: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
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
    notification_channels: Mapped[list["ProjectNotificationChannel"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    user_mappings: Mapped[list["UserMapping"]] = relationship(
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
    negative_prompt: Mapped["ProjectNegativePrompt | None"] = relationship(
        back_populates="project",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
    )
