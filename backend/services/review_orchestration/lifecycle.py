"""MR 生命周期动作（close / merge / reopen）与 reuse 短路路径。

PR3 of the orchestration split：把散在 persistence.py / planning.py 的
"不跑 engine 的短路路径"集中到这里 ——
  - close / merge / reopen 以 Command 模式封装（:class:`LifecycleAction` 协议 +
    ``_LIFECYCLE_ACTIONS`` 注册表），orchestrator 查表分发，不再写 if 链；
  - reuse（head 未变的 CI 重跑，原 planning.py ``_handle_reuse``）同样不跑
    engine，语义同族，一并搬入。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from core.block_policy import BlockPolicyLike, compute_has_blocker
from core.summary_builder import build_review_summary_note
from integrations.gitlab.client import GitLabClient
from models.review import Review as ReviewRow
from repositories.project import ProjectRepository
from repositories.review import FindingRepository
from services.review_orchestration.diff_utils import (
    _extract_int,
    _finding_row_to_engine,
)
from services.review_orchestration.events import GitLabMergeRequestEvent
from services.review_orchestration.gitlab_feedback import _build_review_detail_url
from services.review_orchestration.results import OrchestratorResult, _ReviewPlan

if TYPE_CHECKING:
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)


class LifecycleAction(Protocol):
    """MR 生命周期动作的 Command 接口。

    每个动作实现 :meth:`apply`；返回 :class:`OrchestratorResult` 表示短路返回
    （不再走常规审查流程），返回 ``None`` 表示继续常规审查流程（reopen）。
    """

    async def apply(
        self,
        event: GitLabMergeRequestEvent,
        *,
        session_factory: SessionFactory | None,
    ) -> OrchestratorResult | None:
        """处理一次生命周期事件。"""
        ...


class MrClosedAction:
    """``close`` 动作：MR 关闭（非合并）时的 finding 批量翻状态。"""

    async def apply(
        self,
        event: GitLabMergeRequestEvent,
        *,
        session_factory: SessionFactory | None,
    ) -> OrchestratorResult:
        """MR closed（非合并）：批量把 (project, mr) 的 open finding 标 ``mr_closed``。

        - 不跑 engine；不调 GitLab changes / note / commit status。
        - 插入一条 lifecycle 记账 Review 行（``status='done'``、
          ``review_mode='full'``、``finding_count=0``、``has_blocker=False``），保留时间线。
        - **不动 GitLab 侧 discussion**：MR 关闭时 GitLab 自己会灰化 discussion。
        - session_factory 未接入时全部 no-op，返回一个合成的 result 让上层保持
          "processed=True" 语义。

        Returns:
            :class:`OrchestratorResult`：``finding_count`` 报"受影响的 finding 数"，
            让 webhook 响应能被观测到；其余字段沿用 done 语义。
        """

        return await _handle_lifecycle_event(
            event=event,
            terminal_status="mr_closed",
            log_label="mr_closed",
            session_factory=session_factory,
        )


class MrMergedAction:
    """``merge`` 动作：MR 合并时的 finding 批量翻状态。"""

    async def apply(
        self,
        event: GitLabMergeRequestEvent,
        *,
        session_factory: SessionFactory | None,
    ) -> OrchestratorResult:
        """MR merged：批量把 (project, mr) 的 open finding 标 ``resolved``。

        与 close 分支的区别：finding 状态换成 ``resolved`` 并把
        ``resolved_in_review_id`` 指向本次 lifecycle 记账 review（``resolved`` 语义
        本来就要求带 resolver）。其余流程一致。
        """

        return await _handle_lifecycle_event(
            event=event,
            terminal_status="resolved",
            log_label="mr_merged",
            session_factory=session_factory,
        )


class MrReopenedAction:
    """``reopen`` 动作：MR 重新打开时把 mr_closed finding 翻回 open。"""

    async def apply(
        self,
        event: GitLabMergeRequestEvent,
        *,
        session_factory: SessionFactory | None,
    ) -> None:
        """MR reopen：把之前标记的 mr_closed 翻回 open，不插 Review 行。

        reopen 会紧接着继续跑常规增量审查，该审查会自己产出一条新 review；
        这里只做纯粹的翻转，避免记两条历史。DB 异常吞掉，退化为 no-op（下面
        的常规流程正常继续跑）。固定返回 ``None``，orchestrator 据此 fall
        through 到常规审查流程。
        """

        if session_factory is None:
            return
        try:
            async with session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
                if project is None:
                    return
                finding_repo = FindingRepository(session)
                affected = await finding_repo.reopen_mr_closed(project.id, str(event.mr_iid))
                await session.commit()
                if affected:
                    logger.info(
                        "mr reopened; findings flipped back to open",
                        extra={
                            "gitlab_project_id": event.project_id,
                            "mr_iid": event.mr_iid,
                            "affected_findings": affected,
                        },
                    )
        except SQLAlchemyError:
            logger.exception(
                "failed to reopen mr_closed findings",
                extra={
                    "gitlab_project_id": event.project_id,
                    "mr_iid": event.mr_iid,
                },
            )


_LIFECYCLE_ACTIONS: dict[str, LifecycleAction] = {
    "close": MrClosedAction(),
    "merge": MrMergedAction(),
    "reopen": MrReopenedAction(),
}


def get_lifecycle_action(action: str) -> LifecycleAction | None:
    """按 webhook action 查生命周期 Command。

    未知 action（``open`` / ``update`` 等常规审查动作）返回 ``None``，调用方
    直接走常规审查流程。
    """

    return _LIFECYCLE_ACTIONS.get(action)


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


async def _handle_reuse(
    *,
    event: GitLabMergeRequestEvent,
    plan: _ReviewPlan,
    policy_applied: str,
    block_policy: BlockPolicyLike,
    session_factory: SessionFactory | None,
    gitlab_client: GitLabClient,
    review_detail_base_url: str | None,
) -> OrchestratorResult | None:
    """head 未变的 CI 重跑：跳过 engine，把 parent review 结果重发 GitLab。

    - 不新建 review 行（避免同 head 产生 N 份重复历史）。
    - 重发 note：内容按 parent review 的 findings + 一个"复用上一次"横幅。
    - 重发 commit status：按 has_blocker 决定 state。
    - parent 找不到 / DB 异常时返回 None，让主流程降级走 full 重审。

    has_blocker / blocker_count 从 parent 的 findings 重算（而非直接读 parent
    review 行的 has_blocker / finding_count），确保 blocker 数量精确而非将
    finding 总数误当 blocker 数。
    """
    parent_id = plan.parent_review_id

    logger.info(
        "handle reuse",
        extra={"parent_review_id": parent_id, "block_policy": block_policy},
    )

    if parent_id is None or session_factory is None:
        return None
    try:
        async with session_factory() as session:
            parent = await session.get(ReviewRow, parent_id)
            if parent is None:
                return None
            finding_repo = FindingRepository(session)
            parent_findings_rows = await finding_repo.list_by_review(parent_id)
    except SQLAlchemyError:
        logger.exception(
            "reuse lookup failed; will fall back to full review",
            extra={"parent_review_id": str(parent_id)},
        )
        return None

    logger.info("handle reuse", extra={"parent_findings_rows": parent_findings_rows})

    engine_findings = [_finding_row_to_engine(row) for row in parent_findings_rows]
    logger.info("handle reuse", extra={"findings": engine_findings})
    has_blocker, blocker_count = compute_has_blocker(engine_findings, block_policy)
    logger.info(
        "handle reuse",
        extra={"has_blocker": has_blocker, "blocker_count": blocker_count},
    )
    note = await gitlab_client.create_merge_request_note(
        project_id=event.project_id,
        mr_iid=event.mr_iid,
        body=build_review_summary_note(
            review_id=parent.id,
            findings=engine_findings,
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            policy_applied=policy_applied,
            detail_url=_build_review_detail_url(
                review_detail_base_url=review_detail_base_url, review_id=parent.id,
            ),
            review_mode="reuse",
            mode_reason=plan.reason,
        ),
    )
    await gitlab_client.set_commit_status(
        project_id=event.project_id,
        commit_sha=event.source_commit_sha,
        state="failed" if has_blocker else "success",
        name="ai-code-reviewer",
        description=(
            f"AI Review reused: {len(engine_findings)} finding(s), "
            f"{blocker_count} blocking"
            if has_blocker
            else f"AI Review reused: {len(engine_findings)} finding(s)"
        ),
        target_url=_build_review_detail_url(
            review_detail_base_url=review_detail_base_url, review_id=parent.id,
        ),
    )
    return OrchestratorResult(
        review_id=parent.id,
        project_uuid=event.project_uuid,
        status=parent.status,
        finding_count=len(engine_findings),
        has_blocker=has_blocker,
        blocker_count=blocker_count,
        policy_applied=policy_applied,
        note_id=_extract_int(note, "id"),
    )
