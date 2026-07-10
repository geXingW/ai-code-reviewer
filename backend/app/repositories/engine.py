"""审核引擎配置仓储。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.engine import Engine
from app.repositories.base import BaseRepository


class EngineRepository(BaseRepository[Engine]):
    """Engine 专用查询。"""

    model = Engine

    async def get_by_name(self, name: str) -> Engine | None:
        """按名称精确匹配 Engine 配置。"""

        stmt = select(Engine).where(Engine.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
