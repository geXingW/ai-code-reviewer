"""Review 与 Finding 仓储。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.models.finding import Finding
from app.models.review import Review
from app.repositories.base import BaseRepository


class ReviewRepository(BaseRepository[Review]):
    """Review 专用查询。"""

    model = Review

    async def list_by_project(self, project_id: UUID) -> list[Review]:
        """按项目倒序列出评审记录。"""

        stmt = (
            select(Review)
            .where(Review.project_id == project_id)
            .order_by(Review.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class FindingRepository(BaseRepository[Finding]):
    """Finding 专用查询。"""

    model = Finding

    async def list_by_review(self, review_id: UUID) -> list[Finding]:
        """按 review_id 列出全部 finding。"""

        stmt = select(Finding).where(Finding.review_id == review_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
