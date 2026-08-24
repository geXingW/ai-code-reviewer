"""SQLAlchemy model for role permissions."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base

if TYPE_CHECKING:
    from models.role import Role


class RolePermission(Base):
    """A single permission string assigned to a role."""

    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission", name="uniq_role_permission"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    role_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission: Mapped[str] = mapped_column(
        String(128), nullable=False
    )

    role: Mapped[Role] = relationship(
        back_populates="permissions"
    )