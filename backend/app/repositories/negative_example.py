"""负例库仓储。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.models.negative_example import NegativeExample
from app.repositories.base import BaseRepository


class NegativeExampleRepository(BaseRepository[NegativeExample]):
    """NegativeExample 专用查询。"""

    model = NegativeExample

    async def list_by_project(self, project_id: UUID) -> list[NegativeExample]:
        """按项目列出全部负例。"""

        stmt = select(NegativeExample).where(NegativeExample.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
