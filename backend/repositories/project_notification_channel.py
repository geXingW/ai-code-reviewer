"""项目通知渠道仓储。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from models.project_notification_channel import ProjectNotificationChannel
from repositories.base import BaseRepository


class ProjectNotificationChannelRepository(BaseRepository[ProjectNotificationChannel]):
    """ProjectNotificationChannel 专用查询。"""

    model = ProjectNotificationChannel

    async def get_by_project(self, project_id: UUID) -> list[ProjectNotificationChannel]:
        """列出项目下的所有通知渠道（含已禁用）。"""

        stmt = select(ProjectNotificationChannel).where(
            ProjectNotificationChannel.project_id == project_id,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_enabled_by_project(
        self,
        project_id: UUID,
    ) -> list[ProjectNotificationChannel]:
        """列出项目下所有启用的通知渠道，供 Review 完成后推送使用。"""

        stmt = select(ProjectNotificationChannel).where(
            ProjectNotificationChannel.project_id == project_id,
            ProjectNotificationChannel.enabled.is_(True),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
