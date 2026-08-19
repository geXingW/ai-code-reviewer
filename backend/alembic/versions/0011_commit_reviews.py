"""commit 级审查（Push Hook 逐 commit 审查）的 reviews 表改造。

- ``reviews.mr_iid`` 改为 nullable：commit 审查没有 MR 概念，落 NULL。
  现有按 ``mr_iid`` 等值查询（find_last_review_in_mr / list_open_by_mr）
  天然不匹配 NULL 行，索引 ``ix_reviews_project_mr`` 保留不动。
- 新增 ``reviews.review_kind``：``'mr'``（MR 事件触发，老数据默认值）/
  ``'commit'``（Push Hook 逐 commit 审查）。NOT NULL + server_default='mr'
  保证老数据迁移后有值。

跨方言（PostgreSQL / MySQL 8.0）：只用 ``sa.String`` 通用类型 + server_default
兜底，不做数据回填；``alter_column`` 的 nullable 变更两种方言都支持。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_commit_reviews"
down_revision: str | Sequence[str] | None = "0010_project_negative_prompts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """mr_iid 放宽为 nullable + 新增 review_kind 来源标记列。"""

    op.alter_column(
        "reviews",
        "mr_iid",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.add_column(
        "reviews",
        sa.Column(
            "review_kind",
            sa.String(length=10),
            server_default=sa.text("'mr'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """回滚：删 review_kind 列；mr_iid 恢复 NOT NULL。

    注意：downgrade 时若已存在 mr_iid=NULL 的 commit 审查行，恢复 NOT NULL 会
    失败--需要先手工清理这些行，这是预期内的运维操作。
    """

    op.drop_column("reviews", "review_kind")
    op.alter_column(
        "reviews",
        "mr_iid",
        existing_type=sa.String(length=255),
        nullable=False,
    )
