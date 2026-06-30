"""SQLAlchemy model for LLM providers."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, Float, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.encryption import EncryptedString

if TYPE_CHECKING:
    from app.models.project import Project


class Provider(Base, TimestampMixin):
    """LLM provider credentials and model configuration."""

    __tablename__ = "providers"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    protocol: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    temperature: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        server_default=text("0"),
        nullable=False,
    )
    max_tokens: Mapped[int] = mapped_column(
        Integer,
        default=4096,
        server_default=text("4096"),
        nullable=False,
    )
    extra_headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )

    projects: Mapped[list["Project"]] = relationship(back_populates="provider", lazy="selectin")
