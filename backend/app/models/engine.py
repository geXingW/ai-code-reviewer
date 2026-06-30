"""SQLAlchemy model for review engines."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.project import Project


class Engine(Base, TimestampMixin):
    """Review engine configuration stored in the engine pool."""

    __tablename__ = "engines"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    engine_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )

    projects: Mapped[list["Project"]] = relationship(back_populates="engine", lazy="selectin")
