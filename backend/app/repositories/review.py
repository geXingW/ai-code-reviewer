"""Review 与 Finding 仓储。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, update
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

        .. deprecated::
            自增量审查引入后，主流程不再基于全局 commit_sha 去重（不同 MR 可能引用
            同一 commit），保留此函数仅供未来诊断脚本 / 兼容性回退使用。同 MR
            同 head 复用改走 :meth:`find_last_review_in_mr` + orchestrator 的
            reuse 分支。

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

    async def find_last_review_in_mr(
        self,
        project_id: UUID,
        mr_iid: str,
        exclude_status: tuple[str, ...] = (),
    ) -> Review | None:
        """按 ``(project_id, mr_iid)`` 找同一 MR 的最近一次评审，用于增量串链。

        选择 "created_at DESC + limit 1"：一个 MR 短时间内多次触发，最新一条一定
        是我们要接上的 parent。走 ``ix_reviews_project_mr`` 联合索引，无 N+1。

        Args:
            project_id: DB 中 Project 主键 UUID。
            mr_iid: 归一化后的 MR IID（Review.mr_iid 落库时已是 ``str``）。
            exclude_status: 需要排除的 ``status`` 集合。默认不排除；调用方一般
                传 ``("pending",)`` 跳过未完成评审，避免用未落地的评审做起点。

        Returns:
            最近一条评审；无返回 ``None``。
        """

        conditions = [Review.project_id == project_id, Review.mr_iid == mr_iid]
        if exclude_status:
            conditions.append(Review.status.notin_(exclude_status))
        stmt = (
            select(Review)
            .where(*conditions)
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

    async def list_open_by_mr(
        self,
        project_id: UUID,
        mr_iid: str,
    ) -> list[Finding]:
        """列出同 (project, mr) 累计到目前仍 open 的 finding，供增量合并。

        走 Finding → Review 的 join，只保留 ``Finding.status == 'open'`` 的行，
        按 ``created_at ASC`` 返回（早发现的排前面，展示时用来做"历史遗留"标注
        的稳定顺序）。

        Args:
            project_id: DB 中 Project 主键 UUID。
            mr_iid: MR IID（str）。

        Returns:
            按创建时间升序的 Finding 列表，可能为空。
        """

        stmt = (
            select(Finding)
            .join(Review, Finding.review_id == Review.id)
            .where(
                Review.project_id == project_id,
                Review.mr_iid == mr_iid,
                Finding.status == "open",
            )
            .order_by(Finding.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_resolved(
        self,
        finding_ids: Sequence[UUID],
        resolved_in_review_id: UUID,
    ) -> None:
        """把一批 finding 标记为已解决。

        单条 UPDATE 语句用 ``IN`` 条件一次搞定，避免逐条查询触发 N+1。
        事务 flush()/commit() 由调用方在同一 session 内负责。

        Args:
            finding_ids: 要标记的 finding 主键集合；空集合直接返回，不发 SQL。
            resolved_in_review_id: 本次判定它们已解决的 review 主键。
        """

        if not finding_ids:
            return
        stmt = (
            update(Finding)
            .where(Finding.id.in_(list(finding_ids)))
            .values(status="resolved", resolved_in_review_id=resolved_in_review_id)
        )
        await self._session.execute(stmt)

    async def mark_mr_closed(
        self,
        project_id: UUID,
        mr_iid: str,
        lifecycle_review_id: UUID,
    ) -> int:
        """把 (project, mr) 所有 ``status='open'`` 的 finding 批量标 ``mr_closed``。

        MR 关闭（非合并）时使用：这些 finding 已经跟着"作废"的 MR 一起没意义。
        ``resolved_in_review_id`` 复用来记录"是哪次 lifecycle 事件把它关掉的"，
        语义上稍微 overload 但避免多加一列；后续 reopen 时会把它清空。

        单条 UPDATE + Finding→Review IN 子查询，不做 N+1。

        Args:
            project_id: DB 中 Project 主键 UUID。
            mr_iid: 归一化后的 MR IID（str）。
            lifecycle_review_id: 本次 lifecycle 事件对应的记账 Review 主键。

        Returns:
            实际 UPDATE 命中的行数。
        """

        subq = (
            select(Review.id)
            .where(Review.project_id == project_id, Review.mr_iid == mr_iid)
            .scalar_subquery()
        )
        stmt = (
            update(Finding)
            .where(Finding.review_id.in_(subq), Finding.status == "open")
            .values(status="mr_closed", resolved_in_review_id=lifecycle_review_id)
        )
        result = await self._session.execute(stmt)
        # SQLAlchemy 2.x 保证 update.rowcount；MySQL / PG 都填。返回 0 时上层可打日志。
        return int(getattr(result, "rowcount", 0) or 0)

    async def reopen_mr_closed(
        self,
        project_id: UUID,
        mr_iid: str,
    ) -> int:
        """把 (project, mr) 所有 ``status='mr_closed'`` 的 finding 翻回 ``open``。

        MR reopen 时使用：清空 ``resolved_in_review_id`` 让它回到"活着"状态，
        紧接着上层继续走常规增量审查流程，engine 输出会再决定后续状态。

        Args:
            project_id: DB 中 Project 主键 UUID。
            mr_iid: 归一化后的 MR IID（str）。

        Returns:
            实际 UPDATE 命中的行数。
        """

        subq = (
            select(Review.id)
            .where(Review.project_id == project_id, Review.mr_iid == mr_iid)
            .scalar_subquery()
        )
        stmt = (
            update(Finding)
            .where(Finding.review_id.in_(subq), Finding.status == "mr_closed")
            .values(status="open", resolved_in_review_id=None)
        )
        result = await self._session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)
