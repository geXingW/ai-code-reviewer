"""Best-effort notification push for MR / commit review completion."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from engines import Finding
from services.review_orchestration.diff_utils import _build_findings_summary
from services.review_orchestration.events import GitLabMergeRequestEvent, _CommitLikeEvent
from services.review_orchestration.gitlab_feedback import _build_review_detail_url
from services.review_orchestration.results import _ReviewPlan

if TYPE_CHECKING:
    from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


async def _push_review_notification(
    *,
    event: GitLabMergeRequestEvent,
    review_id: UUID,
    finding_count: int,
    has_blocker: bool,
    blocker_count: int,
    status_value: str,
    findings: Sequence[Finding] | None = None,
    plan: _ReviewPlan | None = None,
    notification_service: NotificationService | None = None,
    review_detail_base_url: str | None = None,
) -> None:
    """推送 Review 完成通知（best-effort，失败不影响主流程）。

    成功 / 引擎异常两条路径共用：把评审摘要交给 :class:`NotificationService`，
    由其按项目配置的渠道分发。除旧有的计数字段外，还带上 MR 链接、作者信息
    （供 @ 创建人）、按严重级别分组的 ``findings_summary``（供正文列表）、
    MR 维度信息（标题 / 创建人 / 创建时间 / 链接）与变更文件数（供
    「MR信息 / 审查摘要」区块渲染）。
    未注入 ``notification_service`` 时直接跳过；任何异常（含推送失败）都被
    吞成 warning 日志，绝不阻断 Review 主流程。
    """

    if notification_service is None:
        return
    # incremental 模式下 plan.changed_files 才有值；full / None 时按 0 降级，
    # 通知侧对 0 会跳过「变更规模」行。
    changed_files_count = (
        len(plan.changed_files) if plan is not None and plan.changed_files else 0
    )
    try:
        await notification_service.send_review_completed(
            gitlab_project_id=event.project_id,
            review_data={
                "review_id": str(review_id),
                "review_kind": "mr",
                "mr_iid": event.mr_iid,
                "mr_title": event.title,
                "finding_count": finding_count,
                "has_blocker": has_blocker,
                "blocker_count": blocker_count,
                "detail_url": _build_review_detail_url(
                    review_detail_base_url=review_detail_base_url,
                    review_id=review_id,
                ),
                "status": status_value,
                "mr_author_username": event.author_username,
                "mr_author_name": event.author_name,
                "gitlab_web_url": event.web_url,
                "findings_summary": _build_findings_summary(findings or []),
                "mr_created_at": event.created_at,
                "changed_files_count": changed_files_count,
            },
        )
    except Exception as exc:
        logger.warning("Failed to send review notification", exc_info=exc)


async def _push_commit_review_notification(
    *,
    event: _CommitLikeEvent,
    review_id: UUID,
    finding_count: int,
    has_blocker: bool,
    blocker_count: int,
    status_value: str,
    findings: Sequence[Finding] | None = None,
    notification_service: NotificationService | None = None,
    review_detail_base_url: str | None = None,
) -> None:
    """推送 commit 审查完成通知（best-effort，失败不影响主流程）。

    参照 :meth:`_push_review_notification`，但 commit 审查没有 MR 上下文，
    ``mr_iid`` / ``mr_title`` 等 MR 语义字段用 commit 信息替代，并标记
    ``review_kind="commit"`` 供通知侧区分标题（Commit 短 SHA 而非 MR 编号）；
    ``gitlab_web_url`` 取 head commit 的页面链接（webhook ``commit.url``）。
    push 事件（含 ``commits`` 属性）还会把完整 commit 列表透传给通知侧，
    供「提交信息」区块逐条列出并 @ 各 commit 作者（参照
    AI-Codereview-Gitlab 的多提交展示方式）；单 commit 事件无此字段，
    通知侧回退单条展示。
    未注入 ``notification_service`` 时直接跳过；任何异常（含推送失败）都被
    吞成 warning 日志，绝不阻断 commit 审查主流程。
    """

    if notification_service is None:
        logger.info("Notification service is not injected")
        return
    logger.info("Pushing commit review notification")
    try:
        await notification_service.send_review_completed(
            gitlab_project_id=event.project_id,
            review_data={
                "review_id": str(review_id),
                "review_kind": "commit",          # 通知侧用 Commit 短 SHA 标题
                "mr_iid": event.commit_sha[:8],  # commit 短 SHA 作为标识
                "mr_title": event.title,          # commit message 首行
                "finding_count": finding_count,
                "has_blocker": has_blocker,
                "blocker_count": blocker_count,
                "detail_url": _build_review_detail_url(
                    review_detail_base_url=review_detail_base_url,
                    review_id=review_id,
                ),
                "status": status_value,
                "mr_author_username": event.author_username,
                "mr_author_name": event.author_name,
                "gitlab_web_url": event.web_url,  # head commit 的 GitLab 链接
                "findings_summary": _build_findings_summary(findings or []),
                "mr_created_at": event.created_at,
                "changed_files_count": 0,
                # 多 commit push 通知：逐条列出并 @ 各 commit 作者；
                # 单 commit 事件无该属性，通知侧回退单条展示。
                "commits": getattr(event, "commits", None),
            },
        )
    except Exception as exc:
        logger.warning("Failed to send commit review notification", exc_info=exc)
