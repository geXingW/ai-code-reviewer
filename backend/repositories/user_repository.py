"""Repository for User model."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from models.role import Role
from models.user import User
from repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository for User CRUD and queries."""

    model = User

    async def get_by_username(self, username: str) -> User | None:
        """Find a user by username (case-sensitive)."""
        stmt = (
            select(User)
            .options(
                selectinload(User.role).selectinload(Role.permissions),
                selectinload(User.project_assignments),
            )
            .where(User.username == username)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_with_relations(self, user_id: UUID) -> User | None:
        """Get a user with role, permissions, and project assignments eager-loaded."""
        stmt = (
            select(User)
            .options(
                selectinload(User.role).selectinload(Role.permissions),
                selectinload(User.project_assignments),
            )
            .where(User.id == user_id)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()