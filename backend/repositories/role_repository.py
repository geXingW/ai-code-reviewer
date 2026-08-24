"""Repository for Role model."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from models.role import Role
from repositories.base import BaseRepository


class RoleRepository(BaseRepository[Role]):
    """Repository for Role CRUD and queries."""

    model = Role

    async def get_by_name(self, name: str) -> Role | None:
        """Find a role by name."""
        stmt = (
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.name == name)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_with_permissions(self, role_id: UUID) -> Role | None:
        """Get a role with permissions eager-loaded."""
        stmt = (
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.id == role_id)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def count_users(self, role_id: UUID) -> int:
        """Count users assigned to this role."""
        from models.user import User

        stmt = select(func.count()).select_from(User).where(User.role_id == role_id)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())