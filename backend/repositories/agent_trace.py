"""Repository for llm-agent execution trace events."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from models.agent_trace_event import AgentTraceEvent
from repositories.base import BaseRepository


class AgentTraceEventRepository(BaseRepository[AgentTraceEvent]):
    """读取 agent_trace_events 表的仓储。写入走 engines.llm_agent.trace.DbSink。"""

    model = AgentTraceEvent

    async def list_by_review(self, review_id: UUID) -> list[AgentTraceEvent]:
        """按 seq 升序返回一次 review 的完整轨迹。"""

        stmt = (
            select(AgentTraceEvent)
            .where(AgentTraceEvent.review_id == review_id)
            .order_by(AgentTraceEvent.seq.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
