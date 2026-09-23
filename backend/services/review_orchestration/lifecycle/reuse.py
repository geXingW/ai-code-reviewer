"""head 未变的 CI 重跑（reuse）短路路径：重发 parent review 结果。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from core.block_policy import BlockPolicyLike, compute_has_blocker
from core.summary_builder import build_review_summary_note
from integrations.gitlab.client import GitLabClient
from models.review import Review as ReviewRow
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
