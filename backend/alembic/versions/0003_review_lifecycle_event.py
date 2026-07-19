"""reviews 表加 lifecycle_event 列，区分 MR 生命周期事件记账与常规审查。

PR #95 之后，MR close / merge webhook 会往 ``reviews`` 表插一条"生命周期记账
Review"（``status='done'`` / ``review_mode='full'`` / ``finding_count=0``）。这条
在 UI 上和普通审查长得一模一样，用户看不出"MR 已关闭 / 已合并"。

本迁移加一列 ``lifecycle_event VARCHAR(20) NULL``：
- ``NULL`` → 普通审查（老数据也无需刷）
- ``'mr_closed'`` → MR 关闭事件的记账
- ``'mr_merged'`` → MR 合并事件的记账

不加索引：查询访问模式是"看单条 review 详情"而不是"过滤所有 mr_closed 记录"。

跨方言（PostgreSQL / MySQL 8.0）：只用 ``sa.String`` 通用类型；nullable 无需
server_default 兜底老数据。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_review_lifecycle_event"
down_revision: str | None = "0002_incremental_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the ``reviews.lifecycle_event`` column."""

    op.add_column(
        "reviews",
        sa.Column("lifecycle_event", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    """Drop the ``reviews.lifecycle_event`` column."""

    op.drop_column("reviews", "lifecycle_event")
