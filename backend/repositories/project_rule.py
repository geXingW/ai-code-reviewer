"""项目规则关联表仓储。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from models.project_rule import ProjectRule
from repositories.base import BaseRepository


class ProjectRuleRepository(BaseRepository[ProjectRule]):
    """ProjectRule 专用查询。"""

    model = ProjectRule

    async def list_by_project(self, project_id: UUID) -> list[ProjectRule]:
        """列出项目下的所有规则关联。"""

        stmt = select(ProjectRule).where(ProjectRule.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
