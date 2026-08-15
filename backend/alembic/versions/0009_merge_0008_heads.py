"""Merge 0008_global_settings and 0008_user_mappings into a single head.

Two separate 0008 migrations were merged to master from different branches,
creating two alembic heads. This merge revision unifies them so ``alembic head``
resolves to a single revision and release SQL generation works correctly.

This is a no-op merge: both migrations add independent tables
(``global_settings`` and ``user_mappings``); no data or schema changes needed.
"""

from collections.abc import Sequence

revision: str = "0009_merge_0008_heads"
down_revision: str | Sequence[str] | None = (
    "0008_global_settings",
    "0008_user_mappings",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op: both parent migrations already create their respective tables."""
    pass


def downgrade() -> None:
    """No-op merge; downgrade just moves back to the two-head state."""
    pass
