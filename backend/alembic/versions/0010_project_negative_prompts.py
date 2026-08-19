"""Project-scoped negative prompts, replacing the global ``negative_prompt`` key.

Upgrade creates ``project_negative_prompts``, seeds every existing project with
the legacy global prompt value (when non-empty), then removes the global key.

数据迁移用纯 SQL（INSERT ... SELECT + CROSS JOIN）实现：
- 跨方言兼容（SQLite / MySQL / PG 均支持 CROSS JOIN 与 CURRENT_TIMESTAMP）；
- 不在 Python 侧读查询结果，alembic offline 模式（release SQL 生成）下
  可直接输出语句而不报错。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_project_negative_prompts"
down_revision: str | Sequence[str] | None = "0009_merge_0008_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建表 + 存量全局提示词下发到每个项目 + 删除全局 key。"""

    op.create_table(
        "project_negative_prompts",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project_id"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
    )

    # 存量数据迁移：旧全局 key 的值非空时，为每个已存在的项目插一条。
    # 表是新建的不存在冲突。
    op.execute(
        "INSERT INTO project_negative_prompts (project_id, content, created_at, updated_at) "
        "SELECT p.id, g.value, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "FROM projects p CROSS JOIN global_settings g "
        "WHERE g.\"key\" = 'negative_prompt' AND g.value <> ''"
    )

    # 全局 key 彻底删除：项目级化后无全局 fallback。
    op.execute("DELETE FROM global_settings WHERE \"key\" = 'negative_prompt'")


def downgrade() -> None:
    """删表。全局 key 不恢复：downgrade 后旧全局配置视为放弃。"""

    op.drop_table("project_negative_prompts")
