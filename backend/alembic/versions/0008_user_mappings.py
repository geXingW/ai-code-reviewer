"""新增 ``user_mappings`` 表：GitLab 用户名 ↔ 钉钉手机号 映射，项目级隔离。

用于 Review 完成通知 @ MR 创建人：按 ``(project_id, gitlab_username)`` 唯一
定位一条映射，取 ``dingtalk_mobile`` 填入钉钉 ``atMobiles``。

跨方言（PostgreSQL / MySQL 8.0）：只用 ``sa.Uuid`` / ``sa.String`` 通用类型；
主键 UUID 由应用层 ``uuid4`` 生成，不依赖 ``gen_random_uuid()``。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_user_mappings"
down_revision: str | None = "0007_project_gitlab_base_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``user_mappings`` table with project-scoped unique constraint."""

    op.create_table(
        "user_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("gitlab_username", sa.String(length=255), nullable=False),
        sa.Column("dingtalk_mobile", sa.String(length=32), nullable=False),
        sa.Column("dingtalk_userid", sa.String(length=128), nullable=True),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "gitlab_username",
            name="uniq_user_mappings_project_gitlab",
        ),
    )
    op.create_index("idx_user_mappings_project_id", "user_mappings", ["project_id"])


def downgrade() -> None:
    """Drop the ``user_mappings`` table."""

    op.drop_index("idx_user_mappings_project_id", table_name="user_mappings")
    op.drop_table("user_mappings")
