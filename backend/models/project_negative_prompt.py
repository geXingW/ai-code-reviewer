"""SQLAlchemy model for project-scoped negative prompt."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from models.project import Project


class ProjectNegativePrompt(Base, TimestampMixin):
    """项目级负样本提示词，一对一。

    每个项目独立配置 / 独立生成 / 独立注入；主键即外键 ``project_id``，
    无自增 id（一对一约束由 PK=FK 天然保证）。
    """

    __tablename__ = "project_negative_prompts"

    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)

    project: Mapped["Project"] = relationship(
        back_populates="negative_prompt",
        uselist=False,
        lazy="selectin",
    )
