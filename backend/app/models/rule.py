"""SQLAlchemy model for shared review rules."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, String, Text, Uuid, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.project_rule import ProjectRule


class Rule(Base, TimestampMixin):
    """Reusable review rule shared across projects."""

    __tablename__ = "rules"

    # 主键 UUID 由 Python 层生成，不依赖 PG 的 gen_random_uuid()，保证 MySQL 也可用。
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    rule_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    prompt_snippet: Mapped[str] = mapped_column(Text, nullable=False)
    severity_default: Mapped[str] = mapped_column(
        String(20),
        default="WARNING",
        server_default=text("'WARNING'"),
        nullable=False,
    )
    # 规则的默认分类；LLM 会以此作为参考直接照抄到 finding.category。
    # 老数据 NULL；seed_rules.py 从 docs/rules-catalog.json 的 category_default
    # 字段读入。允许值参考 FindingCategory 枚举。
    category_default: Mapped[str | None] = mapped_column(String(20), nullable=True)
    languages: Mapped[list[Any]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    path_patterns: Mapped[list[Any]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )
    grace_period_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    project_rules: Mapped[list["ProjectRule"]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
