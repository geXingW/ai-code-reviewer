"""项目屏蔽策略仓储。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.models.project_block_policy import ProjectBlockPolicy
from app.repositories.base import BaseRepository


class ProjectBlockPolicyRepository(BaseRepository[ProjectBlockPolicy]):
    """ProjectBlockPolicy 专用查询。"""

    model = ProjectBlockPolicy

    async def list_by_project(self, project_id: UUID) -> list[ProjectBlockPolicy]:
        """列出项目下的所有屏蔽策略。"""

        stmt = select(ProjectBlockPolicy).where(ProjectBlockPolicy.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
