"""``review_findings`` 与 ``rules`` 两张表分别加 ``category`` / ``category_default`` 列。

PR-B 之前，line-level 评论上的分类 emoji 是通过 ``finding_taxonomy.infer_category``
从 rule_id 静态推断的——LLM 输出的 finding 里没有 category，rules 表也没有
category_default。本迁移把分类从"推断"升级为一等公民字段：

- ``review_findings.category VARCHAR(20) NULL``：LLM 直接输出的 finding 分类；
  NULL 表示"当前 finding 未带上分类"，渲染层会 fallback 到 rule_id 推断。
- ``rules.category_default VARCHAR(20) NULL``：规则的默认分类；LLM 会以此
  作为参考直接照抄到 ``finding.category``。老数据 NULL，seed 或后台维护后逐步
  补全。

允许值语义参见 ``backend/app/core/finding_taxonomy.py::FindingCategory``
（``security`` / ``bug`` / ``performance`` / ``maintainability`` / ``style`` /
``other``）。DB 侧不加 check constraint，只在 API 边界与 seed 逻辑里校验；
未来枚举扩展时不用再动一次 migration。

跨方言（PostgreSQL / MySQL 8.0）：只用 ``sa.String`` 通用类型；nullable 不需要
``server_default`` 兜底老数据——现存行直接为 NULL 由应用层降级处理。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_finding_and_rule_category"
down_revision: str | None = "0003_review_lifecycle_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the ``review_findings.category`` and ``rules.category_default`` columns."""

    op.add_column(
        "review_findings",
        sa.Column("category", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "rules",
        sa.Column("category_default", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    """Drop the newly added category columns."""

    op.drop_column("rules", "category_default")
    op.drop_column("review_findings", "category")
