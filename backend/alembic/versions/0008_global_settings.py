"""Add ``global_settings`` table for runtime-editable key-value settings.

First use case: global system prompt injected into every LLM review call.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_global_settings"
down_revision: str | None = "0007_project_gitlab_base_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``global_settings`` table."""

    op.create_table(
        "global_settings",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    """Drop the ``global_settings`` table."""

    op.drop_table("global_settings")
