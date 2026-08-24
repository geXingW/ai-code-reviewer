"""RBAC 数据模型：users / roles / role_permissions / user_project_assignments。

- ``users``：内部管理账号，username 唯一，role_id 外键 → roles (SET NULL)；
- ``roles``：角色，name 唯一，is_system 标记系统内置角色（不可删）；
- ``role_permissions``：角色 → 权限字符串，唯一 (role_id, permission)；
- ``user_project_assignments``：用户 → 项目授权，唯一 (user_id, project_id)，
  project 外键 CASCADE 删除用户授权。

跨方言（PostgreSQL / MySQL 8.0）：全部使用 ``sa.Uuid`` 通用类型与 ``ondelete``
约束，无 ALTER 隐式提交问题（纯 create_table）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_rbac_users_roles"
down_revision: str | Sequence[str] | None = "0012_project_commit_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the four RBAC tables."""
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("role_id", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_id", "permission", name="uniq_role_permission"),
    )

    op.create_table(
        "user_project_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "project_id", name="uniq_user_project"),
    )


def downgrade() -> None:
    """Drop the four RBAC tables (reverse order of dependencies)."""
    op.drop_table("user_project_assignments")
    op.drop_table("role_permissions")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
    op.drop_table("roles")