"""``projects`` 表新增 ``gitlab_base_url`` 列：GitLab 实例基础地址，支持多实例。

明文存储，非敏感字段。给空串 ``server_default`` 避免老数据行 migration
时报 NOT NULL 约束错误，同时保持与模型 ``nullable=False`` 定义一致。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_project_gitlab_base_url"
down_revision: str | None = "0006_rule_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the ``projects.gitlab_base_url`` column with an empty-string default."""

    op.add_column(
        "projects",
        sa.Column(
            "gitlab_base_url",
            sa.String(length=512),
            server_default="",
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop the ``projects.gitlab_base_url`` column."""

    op.drop_column("projects", "gitlab_base_url")
