"""审核规则仓储。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.rule import Rule
from app.repositories.base import BaseRepository


class RuleRepository(BaseRepository[Rule]):
    """Rule 专用查询。"""

    model = Rule

    async def get_by_rule_id(self, rule_id: str) -> Rule | None:
        """按业务侧规则标识（``rule_id`` 字段）精确匹配。"""

        stmt = select(Rule).where(Rule.rule_id == rule_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
