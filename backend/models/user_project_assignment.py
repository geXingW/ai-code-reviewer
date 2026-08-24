"""SQLAlchemy model for user-project assignments."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base

if TYPE_CHECKING:
    from models.project import Project
    from models.user import User


class UserProjectAssignment(Base):
    """Maps a user to a project they have access to."""

    __tablename__ = "user_project_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uniq_user_project"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    user: Mapped[User] = relationship(
        back_populates="project_assignments"
    )
    project: Mapped[Project] = relationship(
        lazy="selectin"
    )