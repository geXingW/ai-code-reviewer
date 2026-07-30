"""SQLAlchemy model for per-project notification channels (e.g. DingTalk webhooks)."""

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, String, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.encryption import EncryptedString

if TYPE_CHECKING:
    from app.models.project import Project


class ProjectNotificationChannel(Base, TimestampMixin):
    """Per-project outbound notification channel configuration.

    当前优先支持钉钉（DingTalk）机器人 Webhook 推送，``channel_type`` 预留
    ``feishu`` 等未来渠道。``webhook_url`` / ``secret`` 走 :class:`EncryptedString`
    落库加密，读取时自动解密，DB 侧只看到 Fernet 密文。
    """

    __tablename__ = "project_notification_channels"

    # 主键 UUID 由 Python 层生成，不依赖 PG 的 gen_random_uuid()，保证 MySQL 也可用。
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_type: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    webhook_url: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    secret: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )

    project: Mapped["Project"] = relationship(
        back_populates="notification_channels",
        lazy="selectin",
    )
