"""项目仓储。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.project import Project
from app.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy import Select  # noqa: F401 —— 仅用于类型注解


class ProjectRepository(BaseRepository[Project]):
    """Project 专用查询。

    Project 常常需要 eager-load 关联的 rules / block_policies，避免 async 环境下
    ``lazy='select'`` 触发的隐式懒加载。
    """

    model = Project

    @staticmethod
    def _read_select() -> Select[tuple[Project]]:
        """带 eager-load 的完整查询语句，供 API 详情/列表复用。"""

        return select(Project).options(
            selectinload(Project.project_rules),
            selectinload(Project.block_policies),
        )

    async def get_with_relations(self, project_id: UUID) -> Project | None:
        """按主键取项目并预加载 rules / block_policies。"""

        stmt = self._read_select().where(Project.id == project_id)
        result = await self._session.execute(stmt)
        return result.scalars().unique().one_or_none()

    async def list_with_relations(self) -> list[Project]:
        """列出所有项目并预加载 rules / block_policies。"""

        result = await self._session.execute(self._read_select())
        return list(result.scalars().unique().all())

    async def get_by_gitlab_project_id(self, gitlab_project_id: str) -> Project | None:
        """按 GitLab 项目 ID 匹配，用于 webhook 落库前的存在性判断。"""

        stmt = select(Project).where(Project.gitlab_project_id == gitlab_project_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
