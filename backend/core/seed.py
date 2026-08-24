"""Seed super admin on first startup."""

from __future__ import annotations

import logging

from sqlalchemy import select

from core.config import get_settings
from core.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

# 所有权限标识
ALL_PERMISSIONS = [
    # 菜单权限
    "page:dashboard",
    "page:providers",
    "page:global-prompt",
    "page:rules",
    "page:projects",
    "page:user-mappings",
    "page:reviews",
    "page:findings",
    "page:falsePositives",
    "page:negativeExamples",
    "page:engines",
    "page:users",
    "page:roles",
    # 操作权限
    "user:create",
    "user:edit",
    "user:delete",
    "role:create",
    "role:edit",
    "role:delete",
    "project:assign",
]


async def seed_super_admin() -> None:
    """Ensure the super_admin role and admin user exist on first startup."""
    import bcrypt

    from models.role import Role
    from models.role_permission import RolePermission
    from models.user import User

    settings = get_settings()

    async with AsyncSessionLocal() as session:
        # Check if there are any users already
        result = await session.execute(select(User).limit(1))
        if result.scalars().first() is not None:
            logger.info("Users already exist, skipping seed")
            return

        # Create super_admin role
        role = Role(
            name="super_admin",
            description="超级管理员，拥有所有权限",
            is_system=True,
        )
        session.add(role)
        await session.flush()

        # Grant all permissions
        for perm in ALL_PERMISSIONS:
            session.add(RolePermission(role_id=role.id, permission=perm))

        # Create admin user
        admin_user = User(
            username=settings.admin_username,
            password_hash=bcrypt.hashpw(
                settings.admin_password.get_secret_value().encode("utf-8"),
                bcrypt.gensalt(),
            ).decode("utf-8"),
            display_name="管理员",
            role_id=role.id,
            enabled=True,
        )
        session.add(admin_user)

        await session.commit()
        logger.info(
            "Seeded super_admin role and admin user (username=%s)",
            settings.admin_username,
        )