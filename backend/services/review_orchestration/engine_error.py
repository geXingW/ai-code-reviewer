"""Deterministic GitLab feedback when the review engine fails."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from core.block_policy import (
    BlockPolicyLike,
    compute_has_blocker_for_engine_error,
)
from core.summary_builder import build_commit_review_note, build_review_summary_note
from integrations.gitlab.client import GitLabClient
from services.review_orchestration.diff_utils import _extract_int
from services.review_orchestration.events import GitLabMergeRequestEvent, _CommitLikeEvent
from services.review_orchestration.gitlab_feedback import _build_review_detail_url
from services.review_orchestration.notification import (
    _push_commit_review_notification,
    _push_review_notification,
)
from services.review_orchestration.persistence import _persist_review
from services.review_orchestration.results import (
    CommitReviewResult,
    OrchestratorResult,
    _ReviewPlan,
)

if TYPE_CHECKING:
    from services.notification_service import NotificationService
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)


async def _handle_commit_engine_error(
    *,
    event: _CommitLikeEvent,
    review_id: UUID,
    policy_applied: str,
    block_policy: BlockPolicyLike,
    error: Exception,
    gitlab_client: GitLabClient,
    review_detail_base_url: str | None,
    notification_service: NotificationService | None,
) -> CommitReviewResult:
    """commit 审查引擎失败的确定性反馈：失败评论 + failed status + 通知。

    与 MR 流的 :meth:`_handle_engine_error` 语义对齐，但 commit 审查没有
    "阻断合并"概念，**commit status 恒为 failed**（失败不装成功），错误细节
    不回显（防泄漏，只写固定文案）。
    """

    has_blocker, blocker_count = compute_has_blocker_for_engine_error(block_policy)
    note = await gitlab_client.create_commit_comment(
        project_id=event.project_id,
        sha=event.commit_sha,
        note=build_commit_review_note(
            review_id=review_id,
            commit_sha=event.commit_sha,
            commit_title=event.title,
            findings=[],
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            policy_applied=policy_applied,
            detail_url=_build_review_detail_url(
                review_detail_base_url=review_detail_base_url,
                review_id=review_id,
            ),
            engine_error="AI Review engine failed before producing findings.",
        ),
    )
    await gitlab_client.set_commit_status(
        project_id=event.project_id,
        commit_sha=event.commit_sha,
        state="failed",
        name="ai-code-reviewer",
        description="AI Review engine failed",
        target_url=_build_review_detail_url(
            review_detail_base_url=review_detail_base_url,
            review_id=review_id,
        ),
    )
    await _push_commit_review_notification(
        event=event,
        review_id=review_id,
        finding_count=0,
        has_blocker=has_blocker,
        blocker_count=blocker_count,
        status_value="engine_error",
        findings=[],
        notification_service=notification_service,
        review_detail_base_url=review_detail_base_url,
    )
    return CommitReviewResult(
        review_id=review_id,
        project_uuid=event.project_uuid,
        status="engine_error",
        finding_count=0,
        has_blocker=has_blocker,
        note_id=_extract_int(note, "id"),
    )


async def _handle_engine_error(
    *,
    event: GitLabMergeRequestEvent,
    review_id: UUID,
    policy_applied: str,
    block_policy: BlockPolicyLike,
    error: Exception,
    duration_ms: int = 0,
    plan: _ReviewPlan | None = None,
    gitlab_client: GitLabClient,
    review_detail_base_url: str | None,
    notification_service: NotificationService | None,
    default_engine: str,
    session_factory: SessionFactory | None,
) -> OrchestratorResult:
    """Persist deterministic GitLab feedback when the selected engine fails."""

    has_blocker, blocker_count = compute_has_blocker_for_engine_error(block_policy)
    # 引擎失败时用一个"降级"占位 plan：base_sha 兜底到 target_commit_sha，
    # 保证落库时 review_mode / base_sha 仍是合法值。
    effective_plan = plan or _ReviewPlan(
        mode="full",
        base_sha=event.target_commit_sha,
        parent_review_id=None,
        reason="engine_error_no_plan",
    )
    note = await gitlab_client.create_merge_request_note(
        project_id=event.project_id,
        mr_iid=event.mr_iid,
        body=build_review_summary_note(
            review_id=review_id,
            findings=[],
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            policy_applied=policy_applied,
            detail_url=_build_review_detail_url(
                review_detail_base_url=review_detail_base_url,
                review_id=review_id,
            ),
            engine_error="AI Review engine failed before producing findings.",
            review_mode=effective_plan.mode,
            mode_reason=effective_plan.reason,
        ),
    )
    await gitlab_client.set_commit_status(
        project_id=event.project_id,
        commit_sha=event.source_commit_sha,
        state="failed" if has_blocker else "success",
        name="ai-code-reviewer",
        description=(
            "AI Review engine failed and policy blocks merge"
            if has_blocker
            else "AI Review engine failed; policy allows merge"
        ),
        target_url=_build_review_detail_url(
            review_detail_base_url=review_detail_base_url,
            review_id=review_id,
        ),
    )
    # 引擎失败也要落一条 engine_error 记录，方便运营侧统计降级次数。
    await _persist_review(
        event=event,
        review_id=review_id,
        findings=[],
        has_blocker=has_blocker,
        status_value="engine_error",
        duration_ms=duration_ms,
        engine_used=default_engine,
        plan=effective_plan,
        merge=None,
        combined_finding_count=0,
        discussion_ids=None,
        session_factory=session_factory,
    )
    # 推送通知（best-effort，失败不影响主流程）。
    await _push_review_notification(
        event=event,
        review_id=review_id,
        finding_count=0,
        has_blocker=has_blocker,
        blocker_count=blocker_count,
        status_value="engine_error",
        findings=[],
        plan=effective_plan,
        notification_service=notification_service,
        review_detail_base_url=review_detail_base_url,
    )
    return OrchestratorResult(
        review_id=review_id,
        project_uuid=event.project_uuid,
        status="engine_error",
        finding_count=0,
        has_blocker=has_blocker,
        blocker_count=blocker_count,
        policy_applied=policy_applied,
        note_id=_extract_int(note, "id"),
    )
