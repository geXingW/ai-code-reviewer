"""SQLAlchemy model for AI review records."""

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid, false, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.finding import Finding
    from app.models.project import Project
    from app.models.project_block_policy import ProjectBlockPolicy


class Review(Base, TimestampMixin):
    """A single merge request review execution record."""

    __tablename__ = "reviews"

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
    mr_iid: Mapped[str] = mapped_column(String(255), nullable=False)
    source_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    target_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        server_default=text("'pending'"),
        nullable=False,
    )
    engine_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    policy_applied: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("project_block_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    has_blocker: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    finding_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_llm_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 增量审查串链：本次评审的 diff 起点 SHA。full 模式与老数据都可能为 NULL，
    # 由 orchestrator 在有明确 base 时写入；`ix_reviews_project_mr` 索引配合
    # ReviewRepository.find_last_review_in_mr 支撑 O(logN) 的上一次评审查询。
    base_sha: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_review_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("reviews.id", ondelete="SET NULL"),
        nullable=True,
    )
    # review_mode: 'full' | 'incremental'（reuse 模式不新建行，因此不落 'reuse'）。
    # server_default='full' 保证老数据迁移后有值，Python 层 default 保证内存对象取默认值。
    review_mode: Mapped[str] = mapped_column(
        String(20),
        default="full",
        server_default=text("'full'"),
        nullable=False,
    )
    # PR #96：区分"MR 生命周期事件记账 Review"（close / merge webhook 触发）与常规审查。
    # - ``NULL`` → 普通审查（老数据也无需刷）
    # - ``'mr_closed'`` → MR 关闭事件的记账
    # - ``'mr_merged'`` → MR 合并事件的记账
    # 前端有值时改渲染专属徽章，替代（不并列）review_mode 徽章。查询访问模式是
    # "看单条 review 详情"而不是"过滤所有 mr_closed 记录"，不建立索引。
    lifecycle_event: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    project: Mapped["Project"] = relationship(back_populates="reviews", lazy="selectin")
    policy: Mapped["ProjectBlockPolicy | None"] = relationship(
        back_populates="reviews",
        lazy="selectin",
    )
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        # review_findings 表额外有 first_seen_review_id / resolved_in_review_id 两个
        # 指向 reviews.id 的 FK；显式声明 ``findings`` 只跟 review_id 主外键。
        foreign_keys="Finding.review_id",
        lazy="selectin",
    )
