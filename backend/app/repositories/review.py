"""Review 与 Finding 仓储。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

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

    async def list_recent(self, limit: int = 20) -> list[Review]:
        """按 created_at 倒序列出最近 ``limit`` 条评审，预取 project 与 findings。

        用于首页最近审查面板，走 DB 查询以取代早期仅按 POST /api/reviews 入队
        的内存 deque —— webhook 路径也能正确回显。

        Args:
            limit: 上限条数。默认 20。

        Returns:
            按创建时间倒序的 Review 列表；已 selectinload project + findings，
            调用方遍历 findings 统计 BLOCKER 数量不会触发 N+1 查询。
        """

        stmt = (
            select(Review)
            .options(selectinload(Review.project), selectinload(Review.findings))
            .order_by(Review.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_completed_by_project_and_commit(
        self,
        project_id: UUID,
        commit_sha: str,
    ) -> Review | None:
        """按 ``(project_id, commit_sha)`` 查找**已完成**（done / engine_error）评审。

        用于 commit_sha 去重：同一 commit 多次触发（GitLab 重发、Jenkins 重试等）
        时复用旧结果，避免重跑引擎与重写 GitLab 评论。

        Args:
            project_id: DB 中 Project 主键 UUID。
            commit_sha: MR head commit SHA。

        Returns:
            匹配的最近一条 Review；若无返回 ``None``。
        """

        stmt = (
            select(Review)
            .where(
                Review.project_id == project_id,
                Review.commit_sha == commit_sha,
                Review.status.in_(("done", "engine_error")),
            )
            .order_by(Review.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class FindingRepository(BaseRepository[Finding]):
    """Finding 专用查询。"""

    model = Finding

    async def list_by_review(self, review_id: UUID) -> list[Finding]:
        """按 review_id 列出全部 finding。"""

        stmt = select(Finding).where(Finding.review_id == review_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
