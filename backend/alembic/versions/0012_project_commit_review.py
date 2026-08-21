"""projects 表新增项目级 commit 审查配置。

- ``commit_review_enabled`` BOOLEAN NOT NULL DEFAULT FALSE：项目级开关，默认
  关闭（安全第一），由用户在项目配置里主动开启；
- ``commit_review_max_per_push`` INTEGER NOT NULL DEFAULT 10：单次 push 最多
  审查的 commit 数，覆盖全局 settings 同名配置。

不做旧数据回填：全局 ``COMMIT_REVIEW_ENABLED=true`` 的旧行为不自动迁移到
项目级，避免对存量项目误开启。全局 ENV 保留为紧急止血开关（webhook 层
第一道防线），项目级字段是第二道。

跨方言（PostgreSQL / MySQL 8.0）：``sa.false()`` 在两种方言都渲染为合法的
默认值表达式，纯 ``add_column`` 无 ALTER 隐式提交问题。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_project_commit_review"
down_revision: str | Sequence[str] | None = "0011_commit_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """projects 表加项目级 commit 审查开关与单 push 上限。"""

    op.add_column(
        "projects",
        sa.Column(
            "commit_review_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "commit_review_max_per_push",
            sa.Integer(),
            server_default=sa.text("10"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """回滚：删除两个项目级 commit 审查配置列。"""

    op.drop_column("projects", "commit_review_max_per_push")
    op.drop_column("projects", "commit_review_enabled")
