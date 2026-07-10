"""LLM Provider 仓储。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.provider import Provider
from app.repositories.base import BaseRepository


class ProviderRepository(BaseRepository[Provider]):
    """Provider 专用查询。"""

    model = Provider

    async def get_by_name(self, name: str) -> Provider | None:
        """按名称精确匹配 Provider，用于唯一性校验。"""

        stmt = select(Provider).where(Provider.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
