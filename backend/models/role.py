"""SQLAlchemy model for RBAC roles."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from models.role_permission import RolePermission
    from models.user import User


class Role(Base, TimestampMixin):
    """RBAC role with a set of permissions."""

    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    users: Mapped[list[User]] = relationship(
        back_populates="role", lazy="selectin"
    )
    permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="selectin",
    )