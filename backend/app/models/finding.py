"""SQLAlchemy model for line-level AI review findings."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.review import Review


class Finding(Base, TimestampMixin):
    """Line-level issue found during an AI review."""

    __tablename__ = "review_findings"

    # 主键 UUID 由 Python 层生成，不依赖 PG 的 gen_random_uuid()，保证 MySQL 也可用。
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    review_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    line_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    existing_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )
    gitlab_discussion_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fp_status: Mapped[str] = mapped_column(
        String(20),
        default="NONE",
        server_default=text("'NONE'"),
        nullable=False,
    )
    fp_marked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fp_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fp_marked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fp_reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fp_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fp_review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 增量审查合并所需：first_seen_review_id 指向 finding 首次被发现的 review，
    # resolved_in_review_id 指向判定已修的 review；status 用 'open' / 'resolved'。
    # 老数据由 server_default 兜底为 open，兼容迁移。两个外键都设 SET NULL，
    # 单次 review 被删掉（历史清理/重算）不会连带炸掉 finding 记录。
    first_seen_review_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("reviews.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_in_review_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("reviews.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="open",
        server_default=text("'open'"),
        nullable=False,
    )

    # review_findings 上出现了三个指向 reviews.id 的 FK，SQLAlchemy 无法自己
    # 挑主 join 关系；显式 foreign_keys 只声明 ``review`` 走 review_id 主外键。
    review: Mapped["Review"] = relationship(
        back_populates="findings",
        foreign_keys=[review_id],
        lazy="selectin",
    )
