"""``rules`` 表新增 ``tags`` 列：自定义标签（JSON 数组），用于按标签筛选规则。

纯展示维度字段，不参与审查 / 阻断业务逻辑。语义参考
``app.models.rule.Rule.tags``：默认空列表 ``[]``，每条规则可打多个标签
（如 ``security`` / ``performance`` / ``python``）。

跨方言（PostgreSQL / MySQL 8.0）兼容策略：
- 先以 ``nullable=True`` 加列，避免老数据行触发 NOT NULL 约束冲突
  （PG / MySQL 给已存在数据的表加 NOT NULL 列且无默认值都会失败）；
- 用 ``UPDATE ... SET tags = '[]' WHERE tags IS NULL`` 给老数据补默认值
  （PG 隐式 text->json 转换，MySQL 把字符串字面量解析为 JSON，两者都接受）；
- 再 ``alter_column`` 收紧为 ``NOT NULL``，与模型定义保持一致。

不用 ``server_default`` 是因为 MySQL 的 JSON 列默认值需要 ``DEFAULT (expr)``
括号语法，而 SQLAlchemy 的 ``server_default=text("'[]'")`` 会生成不带括号的
``DEFAULT '[]'``，在 MySQL 上会报 "Invalid default value"。分三步走可以
同时避开 PG / MySQL 的默认值语法差异。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_rule_tags"
down_revision: str | None = "0005_project_notification_channel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the ``rules.tags`` JSON column with a backfilled ``[]`` default."""

    # 1. 以 nullable=True 加列，老数据行暂时为 NULL。
    op.add_column("rules", sa.Column("tags", sa.JSON(), nullable=True))
    # 2. 老数据补默认值 []（跨方言：PG 隐式转换、MySQL 字符串解析为 JSON）。
    op.execute("UPDATE rules SET tags = '[]' WHERE tags IS NULL")
    # 3. 收紧为 NOT NULL，与 Rule 模型定义对齐。
    op.alter_column("rules", "tags", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    """Drop the ``rules.tags`` column."""

    op.drop_column("rules", "tags")
