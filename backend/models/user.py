"""SQLAlchemy model for RBAC users."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from models.role import Role
    from models.user_project_assignment import UserProjectAssignment


class User(Base, TimestampMixin):
    """Internal user account for the RBAC management system."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(
        String(256), nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    role_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("roles.id", ondelete="SET NULL"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    role: Mapped[Role | None] = relationship(
        back_populates="users", lazy="selectin"
    )
    project_assignments: Mapped[list[UserProjectAssignment]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )