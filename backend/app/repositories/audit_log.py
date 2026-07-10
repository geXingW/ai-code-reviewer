"""AuditLog 仓储。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    """审计日志专用查询。"""

    model = AuditLog

    async def list_recent(self, *, limit: int = 100) -> list[AuditLog]:
        """按创建时间倒序返回最近 ``limit`` 条日志。"""

        stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
