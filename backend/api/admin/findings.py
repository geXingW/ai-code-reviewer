"""问题（finding）生命周期路由。

包含：finding CRUD、标记误报（``PENDING``）、直接解决、误报审核队列
（``confirm`` / ``reject`` / ``reset``）、负样本库列表。所有会改变阻断状态的
动作都会触发 :func:`_recompute_mr_block_status` 重算 MR 级 has_blocker；
行级评论的 GitLab discussion 同步 resolve。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.block_policy import (
    build_default_block_policies,
    compute_has_blocker,
    match_block_policy,
)
from core.db import DbSession
from models.finding import Finding
from models.negative_example import NegativeExample
from models.project import Project
from models.review import Review
from repositories import (
    FindingRepository,
    ProjectRepository,
    ReviewRepository,
)
from schemas.finding import FindingCreate, FindingRead, FindingUpdate
from schemas.negative_example import NegativeExampleRead

if TYPE_CHECKING:
    from integrations.gitlab.client import GitLabClient

from api.admin.common import (
    Page,
    _commit_or_400,
    _create,
    _delete,
    _get_or_404,
    _paginate,
    _update,
)
from api.admin.deps import (
    _require_admin_auth,
    _require_permission,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["admin"],
    dependencies=[Depends(_require_admin_auth)],
)

class FalsePositiveMarkRequest(BaseModel):
    """Developer false-positive mark payload."""

    marked_by: str = Field(min_length=1, max_length=255)
    reason: str | None = None


class FalsePositiveReviewRequest(BaseModel):
    """Admin false-positive review payload."""

    reviewed_by: str = Field(min_length=1, max_length=255)
    note: str | None = None


@router.get(
    "/findings",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:findings"))],
)
async def list_findings(
    db: DbSession,
    review_id: UUID | None = None,
    severity: str | None = None,
    fp_status: str | None = None,
    file_path: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List review findings with filters used by review detail and FP queue pages."""

    stmt = select(Finding)
    if review_id is not None:
        stmt = stmt.where(Finding.review_id == review_id)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    if fp_status:
        stmt = stmt.where(Finding.fp_status == fp_status)
    if file_path:
        stmt = stmt.where(Finding.file_path.ilike(f"%{file_path}%"))
    return await _paginate(
        db, stmt, FindingRead, "findings", sort, limit, offset,
        enrich=_finding_to_read,
    )


@router.post(
    "/findings",
    response_model=FindingRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_permission("page:findings"))],
)
async def create_finding(payload: FindingCreate, db: DbSession) -> FindingRead:
    """Create a finding for tests and internal admin seeding."""

    finding = Finding(**payload.model_dump())
    return await _create(db, finding, FindingRead, "Finding create failed")


@router.get(
    "/findings/{finding_id}",
    response_model=FindingRead,
    dependencies=[Depends(_require_permission("page:findings"))],
)
async def get_finding(finding_id: UUID, db: DbSession) -> FindingRead:
    """Return one finding by ID."""

    finding = await _get_or_404(db, Finding, finding_id, "Finding")
    return _finding_to_read(finding)


@router.patch(
    "/findings/{finding_id}",
    response_model=FindingRead,
    dependencies=[Depends(_require_permission("page:findings"))],
)
async def update_finding(finding_id: UUID, payload: FindingUpdate, db: DbSession) -> FindingRead:
    """Update one finding for internal admin correction."""

    finding = await _get_or_404(db, Finding, finding_id, "Finding")
    return await _update(db, finding, payload, FindingRead)


@router.delete(
    "/findings/{finding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_permission("page:findings"))],
)
async def delete_finding(finding_id: UUID, db: DbSession) -> None:
    """Delete one finding."""

    await _delete(db, Finding, finding_id, "Finding")


@router.post(
    "/findings/{finding_id}/false-positive",
    response_model=FindingRead,
    dependencies=[Depends(_require_permission("page:findings"))],
)
async def mark_false_positive(
    finding_id: UUID,
    payload: FalsePositiveMarkRequest,
    db: DbSession,
) -> FindingRead:
    """Mark a finding as a pending false-positive candidate."""

    finding = await _get_or_404(db, Finding, finding_id, "Finding")
    finding.fp_status = "PENDING"
    finding.fp_marked_by = payload.marked_by
    finding.fp_marked_reason = payload.reason
    finding.fp_marked_at = datetime.now(UTC)
    finding.fp_reviewed_by = None
    finding.fp_reviewed_at = None
    finding.fp_review_note = None
    await _commit_or_400(db, "False-positive mark failed")
    # refresh 后关系可能已 stale；显式声明 attribute_names 强制重新加载 review，
    # 保证 _finding_to_read 能取到 review / project 上下文（正确性优先）。
    await db.refresh(finding, attribute_names=["review"])
    return _finding_to_read(finding)


class ResolveRequest(BaseModel):
    """标记已解决的请求载荷。"""
    resolved_by: str = Field(..., description="操作人标识")
    reason: str | None = Field(None, description="解决原因")


@router.post(
    "/findings/{finding_id}/resolve",
    response_model=FindingRead,
    dependencies=[Depends(_require_permission("page:findings"))],
)
async def resolve_finding(
    finding_id: UUID,
    payload: ResolveRequest,
    db: DbSession,
) -> FindingRead:
    """直接标记 finding 为已解决，不进误报流程，不关联负样本。

    仅更新 ``status='resolved'`` 并记录操作人/原因，触发 MR 阻断状态重算。
    适用于：
    - 问题已在后续 commit 中修复
    - 团队评估后认为可接受（技术债务）
    - AI 报告的上下文问题但不是系统性误报
    """

    finding = await _get_or_404(db, Finding, finding_id, "Finding")
    if finding.status == "resolved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该问题已为已解决状态",
        )

    finding.status = "resolved"
    finding.fp_marked_by = payload.resolved_by
    finding.fp_marked_reason = payload.reason
    finding.fp_marked_at = datetime.now(UTC)
    # 不更新 fp_status（保持原值或 NONE）
    # 不创建 NegativeExample（不进负样本库）

    await _commit_or_400(db, "Resolve finding failed")
    await db.refresh(finding, attribute_names=["review"])
    await _resolve_finding_discussion(finding, db)
    await _recompute_mr_block_status(finding, db)
    return _finding_to_read(finding)


def _build_gitlab_client(project: Project) -> GitLabClient | None:
    """
    从 Project 构造 GitLabClient，用于后续操作 discussion。
    如果 project.gitlab_access_token 或 project.gitlab_base_url 为空，
    返回 None（skip 操作）。方便测试 monkeypatch 替换。
    """
    if not project.gitlab_access_token or not project.gitlab_base_url:
        return None
    from services.gitlab_client_factory import build_gitlab_client_for_project

    return build_gitlab_client_for_project(project)


async def _resolve_finding_discussion(finding: Finding, db: AsyncSession) -> None:
    """
    确认 / 驳回误报后，尝试 resolve GitLab MR 里的原始 discussion。

    无 gitlab_discussion_id 或 GitLabClient 无法构造 → 直接跳过（老数据兼容）。
    任何 GitLab API 异常 → warning 吞掉，不阻断主流程（误报状态已提交，不回滚）。
    """
    if not finding.gitlab_discussion_id:
        return
    review = await db.get(Review, finding.review_id)
    if not review:
        return
    project_repo = ProjectRepository(db)
    project = await project_repo.get(review.project_id)
    if not project:
        return
    client = _build_gitlab_client(project)
    if client is None:
        return
    try:
        await client.resolve_discussion(
            project_id=int(project.gitlab_project_id),
            mr_iid=int(review.mr_iid or 0),
            discussion_id=finding.gitlab_discussion_id,
            resolved=True,
        )
    except Exception:
        logger.warning(
            "failed to resolve gitlab discussion after fp review",
            extra={
                "finding_id": str(finding.id),
                "gitlab_discussion_id": finding.gitlab_discussion_id,
                "project_id": str(review.project_id),
                "mr_iid": review.mr_iid,
            },
            exc_info=True,
        )


async def _recompute_mr_block_status(finding: Finding, db: AsyncSession) -> None:
    """误报确认/撤销后，重算所属 MR 的阻断状态并更新 GitLab commit status。

    流程：
      1. finding -> review -> project，拿到 block_policies；
      2. 查 MR 最近一次完成的 review（``find_last_review_in_mr``），它的
         ``commit_sha`` 是 GitLab 显示的当前 commit status 所在；
      3. ``list_open_by_mr`` 拿该 MR 当前所有 ``status='open'`` 的 findings
         --已确认误报的 finding 因 ``status='resolved'`` 被自然排除；
      4. ``match_block_policy`` + ``compute_has_blocker`` 重算；
      5. 更新 latest_review.has_blocker 并提交；
      6. 更新 GitLab commit status（failed/success），解除阻断时发一条 note。

    Best-effort：任何异常 warning 吞掉，不回滚已提交的误报状态变更（与
    ``_resolve_finding_discussion`` 同策略）。
    """

    try:
        review = await db.get(Review, finding.review_id)
        if not review:
            return
        project_repo = ProjectRepository(db)
        project = await project_repo.get(review.project_id)
        if not project:
            return

        review_repo = ReviewRepository(db)
        # commit 审查行 mr_iid 为 NULL，等值查询天然不命中 -> 传空串走同样的
        # no-op 路径（latest_review 为 None 时直接 return）。
        mr_iid_key = review.mr_iid or ""
        latest_review = await review_repo.find_last_review_in_mr(
            review.project_id,
            mr_iid_key,
            exclude_status=("pending",),
        )
        if not latest_review:
            return

        finding_repo = FindingRepository(db)
        open_findings = await finding_repo.list_open_by_mr(
            review.project_id,
            mr_iid_key,
        )

        policies = project.block_policies or []
        if not policies:
            policies = build_default_block_policies(project.id)
        block_policy = match_block_policy(policies, latest_review.target_branch)
        if block_policy is None:
            logger.warning(
                "no block policy matched target branch; skip MR block status recompute",
                extra={
                    "project_id": str(project.id),
                    "target_branch": latest_review.target_branch,
                },
            )
            return
        has_blocker, blocker_count = compute_has_blocker(open_findings, block_policy)

        # 只在状态确实变化时才更新 DB + GitLab，避免无谓的 API 调用。
        if latest_review.has_blocker == has_blocker:
            return

        latest_review.has_blocker = has_blocker
        await db.commit()

        client = _build_gitlab_client(project)
        if client is None:
            return
        await client.set_commit_status(
            project_id=int(project.gitlab_project_id),
            commit_sha=latest_review.commit_sha,
            state="failed" if has_blocker else "success",
            name="ai-code-reviewer",
            description=(
                f"{len(open_findings)} finding(s), {blocker_count} blocking finding(s)"
                if has_blocker
                else f"AI Review: {len(open_findings)} finding(s) — false positive resolved"
            ),
            target_url=None,
        )
        if not has_blocker:
            await client.create_merge_request_note(
                project_id=int(project.gitlab_project_id),
                mr_iid=int(latest_review.mr_iid or 0),
                body=(
                    "✅ **误报已确认并解决**\n\n"
                    f"Finding `{finding.rule_id}` 已标记为误报，"
                    "本次 MR 不再被该问题阻断。"
                ),
            )
    except Exception:
        logger.warning(
            "failed to recompute MR block status after fp review",
            extra={
                "finding_id": str(finding.id),
                "review_id": str(finding.review_id),
            },
            exc_info=True,
        )


@router.get(
    "/false-positives/pending",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:falsePositives"))],
)
async def list_pending_false_positives(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "created_at",
) -> Page:
    """List pending false-positive candidates awaiting admin review."""

    stmt = select(Finding).where(Finding.fp_status == "PENDING")
    return await _paginate(db, stmt, FindingRead, "findings", sort, limit, offset)


@router.post(
    "/false-positives/{finding_id}/confirm",
    response_model=FindingRead,
    dependencies=[Depends(_require_permission("page:falsePositives"))],
)
async def confirm_false_positive(
    finding_id: UUID,
    payload: FalsePositiveReviewRequest,
    db: DbSession,
) -> FindingRead:
    """Confirm a false-positive and persist it as a negative prompt example.

    确认误报后同时把 finding 的 ``status`` 标记为 ``resolved``，使其不再
    计入 MR 的阻断 finding 集合。随后调用 ``_recompute_mr_block_status``
    重算 MR 最新 review 的 ``has_blocker`` 并更新 GitLab commit status。
    """

    finding = await _get_pending_finding(db, finding_id)
    finding.fp_status = "CONFIRMED"
    finding.fp_reviewed_by = payload.reviewed_by
    finding.fp_reviewed_at = datetime.now(UTC)
    finding.fp_review_note = payload.note
    # 确认误报 = 该问题不再是问题，标 resolved 让它退出阻断统计。
    finding.status = "resolved"
    finding.resolved_in_review_id = finding.review_id
    review = await db.get(Review, finding.review_id)
    existing_code = finding.existing_code or finding.description or finding.title
    db.add(
        NegativeExample(
            rule_id=finding.rule_id,
            project_id=review.project_id if review else None,
            code_snippet=existing_code,
            explanation=payload.note or finding.fp_marked_reason,
            source_finding_id=finding.id,
            approved_by=payload.reviewed_by,
            approved_at=datetime.now(UTC),
        ),
    )
    await _commit_or_400(db, "False-positive confirm failed")
    await db.refresh(finding, attribute_names=["review"])
    await _resolve_finding_discussion(finding, db)
    await _recompute_mr_block_status(finding, db)
    return _finding_to_read(finding)


@router.post(
    "/false-positives/{finding_id}/reject",
    response_model=FindingRead,
    dependencies=[Depends(_require_permission("page:falsePositives"))],
)
async def reject_false_positive(
    finding_id: UUID,
    payload: FalsePositiveReviewRequest,
    db: DbSession,
) -> FindingRead:
    """Reject a false-positive candidate and retain review audit fields."""

    finding = await _get_pending_finding(db, finding_id)
    finding.fp_status = "REJECTED"
    finding.fp_reviewed_by = payload.reviewed_by
    finding.fp_reviewed_at = datetime.now(UTC)
    finding.fp_review_note = payload.note
    await _commit_or_400(db, "False-positive reject failed")
    await db.refresh(finding, attribute_names=["review"])
    await _resolve_finding_discussion(finding, db)
    return _finding_to_read(finding)


async def _get_reviewed_finding(db: AsyncSession, finding_id: UUID) -> Finding:
    """获取已评审的 finding（CONFIRMED 或 REJECTED），否则抛 400。"""
    finding = await _get_or_404(db, Finding, finding_id, "False-positive candidate")
    if finding.fp_status not in ("CONFIRMED", "REJECTED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Finding is not reviewed (status: {finding.fp_status})",
        )
    return finding


@router.post(
    "/false-positives/{finding_id}/reset",
    response_model=FindingRead,
    dependencies=[Depends(_require_permission("page:falsePositives"))],
)
async def reset_false_positive_review(
    finding_id: UUID,
    db: DbSession,
) -> FindingRead:
    """撤销误报评审结果，状态变回 PENDING，可重新走评审流程。

    如果原状态是 CONFIRMED（finding ``status`` 被设为 ``resolved``），
    撤销时需要重开 finding（``status='open'``）并重算 MR 阻断状态 --
    因为该 finding 重新成为活跃问题，可能再次阻断 MR。
    """

    finding = await _get_reviewed_finding(db, finding_id)

    # 如果是 CONFIRMED 状态，需要删除对应的 NegativeExample
    if finding.fp_status == "CONFIRMED":
        negative_example = await db.scalar(
            select(NegativeExample).where(NegativeExample.source_finding_id == finding.id)
        )
        if negative_example:
            await db.delete(negative_example)

    # 重置评审字段
    old_status = finding.fp_status
    finding.fp_status = "PENDING"
    finding.fp_reviewed_by = None
    finding.fp_reviewed_at = None
    finding.fp_review_note = None

    # CONFIRMED -> PENDING：finding 从 resolved 重开为 open，重新计入阻断统计。
    if old_status == "CONFIRMED":
        finding.status = "open"
        finding.resolved_in_review_id = None

    await _commit_or_400(db, "False-positive reset failed")
    logger.info(
        "Reset false-positive review",
        extra={
            "finding_id": str(finding.id),
            "old_status": old_status,
        },
    )
    await db.refresh(finding, attribute_names=["review"])
    # CONFIRMED -> PENDING 时 finding 重新成为 blocker，需要重算 MR 状态。
    if old_status == "CONFIRMED":
        await _recompute_mr_block_status(finding, db)
    return _finding_to_read(finding)


@router.get(
    "/negative-examples",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:negativeExamples"))],
)
async def list_negative_examples(
    db: DbSession,
    rule_id: str | None = None,
    project_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List approved negative examples generated by false-positive review."""

    stmt = select(NegativeExample)
    if rule_id:
        stmt = stmt.where(NegativeExample.rule_id == rule_id)
    if project_id is not None:
        stmt = stmt.where(NegativeExample.project_id == project_id)
    return await _paginate(db, stmt, NegativeExampleRead, "negative_examples", sort, limit, offset)


def _finding_to_read(finding: Finding) -> FindingRead:
    """构建带 MR / 项目上下文的 FindingRead。

    - ``project_name`` / ``project_id``：从 ``finding.review.project`` 关系读；
    - ``mr_iid`` / ``review_created_at``：从 ``finding.review`` 读；
    - ``mr_title``：Review 表未落库该列（见 FindingRead 注释），保留 None
      以待未来落库后一次性联通。

    ``Finding.review`` 与 ``Review.project`` 都是 ``lazy=selectin``（分别见
    ``app/models/finding.py`` 和 ``app/models/review.py``），list 场景一次性
    加载，无 N+1；单条 refresh 后的调用方需要自行 ``refresh(attribute_names=
    ["review"])`` 以避免 relationship stale。
    """

    read = FindingRead.model_validate(finding)
    review = finding.review
    if review is not None:
        read.mr_iid = review.mr_iid
        read.review_created_at = review.created_at
        if review.project is not None:
            read.project_name = review.project.name
            read.project_id = review.project.id
    return read


async def _get_pending_finding(db: AsyncSession, finding_id: UUID) -> Finding:
    """Return a finding that is currently pending false-positive review."""

    finding = await _get_or_404(db, Finding, finding_id, "Finding")
    if finding.fp_status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finding is not pending false-positive review",
        )
    return finding
