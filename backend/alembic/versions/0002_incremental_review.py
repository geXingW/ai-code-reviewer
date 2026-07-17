"""Incremental review columns for reviews / review_findings.

给同 MR 增量审查串链所需字段建库：reviews 加 (base_sha, parent_review_id,
review_mode) 与 (project_id, mr_iid) 联合索引；review_findings 加
(first_seen_review_id, resolved_in_review_id, status)。

跨方言（PostgreSQL / MySQL 8.0）：只用 ``sa.Uuid`` / ``sa.String`` 通用类型，
NOT NULL 列（``review_mode`` / ``status``）用 ``server_default`` 兜底老数据，
迁移过程本身不做数据回填。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_incremental_review"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add incremental-review columns and the (project_id, mr_iid) lookup index."""

    op.add_column(
        "reviews",
        sa.Column("base_sha", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "reviews",
        sa.Column("parent_review_id", sa.Uuid(), nullable=True),
    )
    # server_default='full' 保证既有行迁移后有明确模式；Python 层 default 保证
    # 新对象在内存里也带默认值。
    op.add_column(
        "reviews",
        sa.Column(
            "review_mode",
            sa.String(length=20),
            server_default=sa.text("'full'"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_reviews_parent_review_id",
        source_table="reviews",
        referent_table="reviews",
        local_cols=["parent_review_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_reviews_project_mr",
        "reviews",
        ["project_id", "mr_iid"],
        unique=False,
    )

    op.add_column(
        "review_findings",
        sa.Column("first_seen_review_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "review_findings",
        sa.Column("resolved_in_review_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "review_findings",
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_review_findings_first_seen_review_id",
        source_table="review_findings",
        referent_table="reviews",
        local_cols=["first_seen_review_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_review_findings_resolved_in_review_id",
        source_table="review_findings",
        referent_table="reviews",
        local_cols=["resolved_in_review_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Drop the incremental-review columns and related indices."""

    op.drop_constraint(
        "fk_review_findings_resolved_in_review_id",
        "review_findings",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_review_findings_first_seen_review_id",
        "review_findings",
        type_="foreignkey",
    )
    op.drop_column("review_findings", "status")
    op.drop_column("review_findings", "resolved_in_review_id")
    op.drop_column("review_findings", "first_seen_review_id")

    op.drop_index("ix_reviews_project_mr", table_name="reviews")
    op.drop_constraint("fk_reviews_parent_review_id", "reviews", type_="foreignkey")
    op.drop_column("reviews", "review_mode")
    op.drop_column("reviews", "parent_review_id")
    op.drop_column("reviews", "base_sha")
