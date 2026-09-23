"""MR 生命周期动作（close / merge / reopen）的 Command 封装与注册表。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.exc import SQLAlchemyError

from repositories.project import ProjectRepository
from repositories.review import FindingRepository
from services.review_orchestration.events import GitLabMergeRequestEvent
from services.review_orchestration.lifecycle.event import _handle_lifecycle_event
from services.review_orchestration.results import OrchestratorResult

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
