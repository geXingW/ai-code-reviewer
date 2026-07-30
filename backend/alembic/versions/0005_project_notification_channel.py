"""新增 ``project_notification_channels`` 表：项目级消息推送渠道配置。

承载钉钉（及未来飞书等）机器人 Webhook 推送配置。``webhook_url`` / ``secret``
为敏感字段，应用层通过 :class:`EncryptedString`（底层 ``Text`` 列）加密存储，
DB 侧不感知密文。

跨方言（PostgreSQL / MySQL 8.0）：只用 ``sa.Uuid`` / ``sa.String`` / ``sa.Boolean``
/ ``sa.Text`` 通用类型；主键 UUID 由应用层 ``uuid4`` 生成，不依赖 ``gen_random_uuid()``。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_project_notification_channel"
down_revision: str | None = "0004_finding_and_rule_category"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``project_notification_channels`` table."""

    op.create_table(
        "project_notification_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("channel_type", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("webhook_url", sa.Text(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Drop the ``project_notification_channels`` table."""

    op.drop_table("project_notification_channels")
