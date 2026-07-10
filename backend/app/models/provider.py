"""SQLAlchemy model for LLM providers."""

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, Float, Integer, String, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.encryption import EncryptedString

if TYPE_CHECKING:
    from app.models.project import Project


class Provider(Base, TimestampMixin):
    """LLM provider credentials and model configuration."""

    __tablename__ = "providers"

    # 主键 UUID 由 Python 层生成，不依赖 PG 的 gen_random_uuid()，保证 MySQL 也可用。
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    protocol: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    temperature: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )
    max_tokens: Mapped[int] = mapped_column(
        Integer,
        default=4096,
        nullable=False,
    )
    extra_headers: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )

    projects: Mapped[list["Project"]] = relationship(back_populates="provider", lazy="selectin")
