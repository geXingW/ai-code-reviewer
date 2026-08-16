"""负例库仓储。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from models.negative_example import NegativeExample
from repositories.base import BaseRepository


class NegativeExampleRepository(BaseRepository[NegativeExample]):
    """NegativeExample 专用查询。"""

    model = NegativeExample

    async def list_by_project(self, project_id: UUID) -> list[NegativeExample]:
        """按项目列出全部负例。"""

        stmt = select(NegativeExample).where(NegativeExample.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_approved(self, limit: int = 100) -> list[NegativeExample]:
        """列出所有已批准的负样本，按 approved_at DESC 排序。"""

        stmt = (
            select(NegativeExample)
            .where(NegativeExample.approved_at.is_not(None))
            .order_by(NegativeExample.approved_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
