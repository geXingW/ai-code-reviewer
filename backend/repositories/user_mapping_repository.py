"""用户映射（GitLab 用户名 ↔ 钉钉手机号）仓储。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from models.user_mapping import UserMapping
from repositories.base import BaseRepository


class UserMappingRepository(BaseRepository[UserMapping]):
    """UserMapping 专用查询与写入。"""

    model = UserMapping

    async def get_by_gitlab_username(
        self,
        project_id: UUID,
        gitlab_username: str,
    ) -> UserMapping | None:
        """按项目 + GitLab 用户名取唯一映射，不存在返回 ``None``。"""

        stmt = select(UserMapping).where(
            UserMapping.project_id == project_id,
            UserMapping.gitlab_username == gitlab_username,
        )
        result = await self._session.execute(stmt)
        return result.scalars().one_or_none()

    async def list_by_project(self, project_id: UUID) -> list[UserMapping]:
        """列出项目下所有映射，按 ``gitlab_username`` 排序保证响应顺序稳定。"""

        stmt = (
            select(UserMapping)
            .where(UserMapping.project_id == project_id)
            .order_by(UserMapping.gitlab_username.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, obj: UserMapping) -> UserMapping:
        """新建映射；``flush()`` 触发唯一约束校验，commit 由上层决定。"""

        return await self.add(obj)

    async def update(
        self,
        obj: UserMapping,
        changes: dict[str, object],
    ) -> UserMapping:
        """按字段字典局部更新映射；commit 由上层决定。

        Args:
            obj: 待更新的 ORM 对象（须已挂载在当前 session）。
            changes: 字段名 -> 新值；仅接受模型列属性，空字典等价 no-op。

        Returns:
            更新后的 ORM 对象（已 flush，约束错误会在此暴露）。
        """

        for field, value in changes.items():
            setattr(obj, field, value)
        await self.flush()
        return obj

    # delete() 直接继承 BaseRepository.delete：删除后 commit 由上层事务边界决定。
