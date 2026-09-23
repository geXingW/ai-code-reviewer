"""close / merge 共用的 lifecycle 记账事务骨架。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from models.review import Review as ReviewRow
from repositories.project import ProjectRepository
from repositories.review import FindingRepository
from services.review_orchestration.events import GitLabMergeRequestEvent
from services.review_orchestration.results import OrchestratorResult

if TYPE_CHECKING:
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)


async def _handle_lifecycle_event(
    *,
    event: GitLabMergeRequestEvent,
    terminal_status: str,
    log_label: str,
    session_factory: SessionFactory | None,
) -> OrchestratorResult:
    """close / merge 共用的落库骨架：写 lifecycle Review + 批量翻状态。

    走一个事务：
      1. 插入 lifecycle Review 行（``status='done'``、``review_mode='full'``、
         ``finding_count=0``、``has_blocker=False``、``duration_ms=0``）；
      2. 调 :meth:`FindingRepository.mark_mr_closed` 或 :meth:`mark_resolved` 走
         单条 UPDATE + IN 子查询把所有 open finding 翻到 ``terminal_status``。

    session_factory / project 缺失均视为 no-op，返回一个合成的 result 而不是
    抛错，让 webhook 响应保持 processed=True。
    """

    review_id = uuid4()
    affected = 0
    if session_factory is not None:
        try:
            async with session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
                if project is None:
                    logger.warning(
                        "skip lifecycle event: project not registered",
                        extra={
                            "gitlab_project_id": event.project_id,
                            "mr_iid": event.mr_iid,
                            "action": event.action,
                        },
                    )
                else:
                    review_row = ReviewRow(
                        id=review_id,
                        project_id=project.id,
                        mr_iid=str(event.mr_iid),
                        source_branch=event.source_branch,
                        target_branch=event.target_branch,
                        commit_sha=event.source_commit_sha,
                        status="done",
                        engine_used=None,
                        has_blocker=False,
                        finding_count=0,
                        duration_ms=0,
                        base_sha=event.target_commit_sha,
                        parent_review_id=None,
                        review_mode="full",
                        # PR #96：区分 lifecycle 记账与常规审查。前端根据此字段
                        # 渲染专属徽章（"MR 已关闭" / "MR 已合并"）。
                        lifecycle_event=(
                            "mr_closed"
                            if terminal_status == "mr_closed"
                            else "mr_merged"
                        ),
                    )
                    session.add(review_row)
                    await session.flush()

                    finding_repo = FindingRepository(session)
                    if terminal_status == "mr_closed":
                        affected = await finding_repo.mark_mr_closed(
                            project.id, str(event.mr_iid), review_id,
                        )
                    else:
                        # merged：先查出 (project, mr) 里所有 open finding 的 id，
                        # 再复用 mark_resolved（保证 resolved_in_review_id 与其它
                        # incremental 走的路径完全一致）。
                        open_rows = await finding_repo.list_open_by_mr(
                            project.id, str(event.mr_iid),
                        )
                        open_ids = [row.id for row in open_rows]
                        affected = len(open_ids)
                        await finding_repo.mark_resolved(open_ids, review_id)
                    await session.commit()
                    logger.info(
                        "mr lifecycle event applied",
                        extra={
                            "gitlab_project_id": event.project_id,
                            "mr_iid": event.mr_iid,
                            "action": event.action,
                            "lifecycle": log_label,
                            "affected_findings": affected,
                            "lifecycle_review_id": str(review_id),
                        },
                    )
        except SQLAlchemyError:
            logger.exception(
                "failed to apply MR lifecycle event",
                extra={
                    "gitlab_project_id": event.project_id,
                    "mr_iid": event.mr_iid,
                    "action": event.action,
                },
            )
    return OrchestratorResult(
        review_id=review_id,
        project_uuid=event.project_uuid,
        status="done",
        # 用受影响 finding 数，webhook 响应侧可观测。
        finding_count=affected,
        has_blocker=False,
        blocker_count=0,
        policy_applied=None,
        note_id=None,
    )
