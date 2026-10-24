"""审查记录的路由（MR / Push / Commit 统一口径）。

- ``/reviews/recent``：首页「最近审查」面板，直接查库，DB 异常时降级到
  :mod:`services.recent_review_buffer`。
- ``/reviews`` 与 ``/reviews/records``：同一份实现，支持来源 / 状态 / 项目 / 搜索过滤。
- ``/reviews/{id}/agent-trace``：llm-agent 引擎的执行轨迹（工具调用、模型响应）。
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from api.admin.common import (
    Page,
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
from core.db import DbSession
from models.review import Review
from repositories import (
    ReviewRepository,
)
from repositories.agent_trace import AgentTraceEventRepository
from schemas.agent_trace import AgentTraceEventRead
from schemas.review import RecentReviewRead, ReviewCreate, ReviewRead, ReviewUpdate
from services import recent_review_buffer

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["admin"],
    dependencies=[Depends(_require_admin_auth)],
)


@router.get(
    "/reviews/recent",
    response_model=list[RecentReviewRead],
    dependencies=[Depends(_require_permission("page:reviews"))],
)
async def list_recent_reviews(db: DbSession) -> list[RecentReviewRead]:
    """从 DB 读最近 20 条评审用于首页最近审查面板（全来源合并口径）。

    DB 是唯一事实源。历史上的内存 deque 只记录 ``POST /api/reviews`` 触发的
    审查（webhook 路径不经过），现已收敛为
    :mod:`services.recent_review_buffer`，仅在本查询异常时作降级兜底。

    feat/unified-review-records 起面板与「审查记录」菜单口径统一：MR /
    push / commit 审查全部展示（``review_kind`` 不过滤），push/commit 行
    的 ``mr_iid`` 为 None、标题用 commit 短 SHA，前端按来源徽章区分。
    """

    try:
        reviews_repo = ReviewRepository(db)
        rows = await reviews_repo.list_recent(limit=20)
    except (SQLAlchemyError, OSError, ConnectionError) as exc:
        # 涵盖 SQLAlchemy 抛出的所有 DB 层错误，以及 asyncpg 连接期未被包装的
        # 原生错误（OSError / ConnectionError），失败时回退到内存 deque 兜底。
        logger.warning("recent reviews DB fallback: %s", exc)
        return recent_review_buffer.snapshot()
    except Exception as exc:  # noqa: BLE001 — DB 拉取失败必须降级，避免面板 500。
        logger.warning("recent reviews DB fallback (unexpected): %s", exc)
        return recent_review_buffer.snapshot()
    return [_recent_review_from_orm(row) for row in rows]


def _recent_review_from_orm(review: object) -> RecentReviewRead:
    """将 Review ORM 行转换为面板摘要 schema。

    - project_path 直接复用 project.name（前端已展示；比 UUID/数字 ID 更可读）。
    - project_id 尝试从 gitlab_project_id 解析为 int，兜底 0。
    - blocker_count 通过 selectinload 的 findings 在 Python 侧统计，避免 N+1。
    """

    project = getattr(review, "project", None)
    project_name = getattr(project, "name", None) or "-"
    gitlab_project_id_raw = getattr(project, "gitlab_project_id", None) if project else None
    try:
        gitlab_project_id = int(gitlab_project_id_raw) if gitlab_project_id_raw is not None else 0
    except (TypeError, ValueError):
        gitlab_project_id = 0

    findings = getattr(review, "findings", []) or []
    blocker_count = sum(1 for f in findings if getattr(f, "severity", "") == "BLOCKER")

    # feat/unified-review-records：push/commit 审查行 mr_iid 为 NULL，保持 None 透传
    # （前端按 review_kind 分支渲染，不再把 NULL 硬转 0 冒充 MR !0）。
    mr_iid_raw = getattr(review, "mr_iid", None)
    try:
        mr_iid = int(mr_iid_raw) if mr_iid_raw is not None else None
    except (TypeError, ValueError):
        mr_iid = None

    # 来源标记：老数据 / MR 行为 'mr'（server_default），push/commit 行为对应值。
    review_kind = getattr(review, "review_kind", None) or "mr"
    # 标题按来源分支：MR 行用 MR 编号，push/commit 行用 commit 短 SHA。
    if review_kind == "mr":
        title = f"MR !{mr_iid_raw}"
    else:
        commit_sha = str(getattr(review, "commit_sha", "") or "")
        title = f"{review_kind.capitalize()} {commit_sha[:7]}"

    return RecentReviewRead(
        review_id=getattr(review, "id", None),
        project_id=gitlab_project_id,
        project_path=project_name,
        mr_iid=mr_iid,
        title=title,
        web_url=None,
        status=getattr(review, "status", "unknown"),
        has_blocker=bool(getattr(review, "has_blocker", False)),
        finding_count=int(getattr(review, "finding_count", 0) or 0),
        blocker_count=blocker_count,
        policy_applied=None,
        review_url=None,
        engine_used=getattr(review, "engine_used", None),
        created_at=getattr(review, "created_at", None),
        review_mode=getattr(review, "review_mode", None) or "full",
        lifecycle_event=getattr(review, "lifecycle_event", None),
        review_kind=review_kind,
    )


@router.get(
    "/reviews",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:reviews"))],
)
@router.get(
    "/reviews/records",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:reviews"))],
)
async def list_reviews(
    db: DbSession,
    project_id: UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    mr_iid: str | None = None,
    review_kind: Annotated[list[str] | None, Query()] = None,
    commit_sha: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List review records with management UI filters.

    ``review_kind`` 支持多值（``?review_kind=commit&review_kind=push``），
    供「Push/Commit 审查」页过滤；不传时不按来源过滤（老行为）。
    ``commit_sha`` 精确匹配（新页面按 SHA 搜索用）。
    """

    stmt = select(Review)
    if project_id is not None:
        stmt = stmt.where(Review.project_id == project_id)
    if status_filter:
        stmt = stmt.where(Review.status == status_filter)
    if mr_iid:
        stmt = stmt.where(Review.mr_iid == mr_iid)
    if review_kind:
        stmt = stmt.where(Review.review_kind.in_(review_kind))
    if commit_sha:
        stmt = stmt.where(Review.commit_sha == commit_sha)
    return await _paginate(
        db, stmt, ReviewRead, "reviews", sort, limit, offset, enrich=_review_to_read
    )


@router.post(
    "/reviews/records",
    response_model=ReviewRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_permission("page:reviews"))],
)
async def create_review_record(payload: ReviewCreate, db: DbSession) -> ReviewRead:
    """Create a review record for tests and internal admin seeding."""

    review = Review(**payload.model_dump())
    return await _create(db, review, ReviewRead, "Review create failed")


@router.get(
    "/reviews/{review_id}",
    response_model=ReviewRead,
    dependencies=[Depends(_require_permission("page:reviews"))],
)
async def get_review_record(review_id: UUID, db: DbSession) -> ReviewRead:
    """Return one review record by ID."""

    review = await _get_or_404(db, Review, review_id, "Review")
    return _review_to_read(review)


@router.get(
    "/reviews/{review_id}/agent-trace",
    response_model=list[AgentTraceEventRead],
    dependencies=[Depends(_require_permission("page:reviews"))],
)
async def get_review_agent_trace(review_id: UUID, db: DbSession) -> list[AgentTraceEventRead]:
    """Return the llm-agent execution trace events of one review, ordered by seq."""

    await _get_or_404(db, Review, review_id, "Review")
    repository = AgentTraceEventRepository(db)
    events = await repository.list_by_review(review_id)
    return [AgentTraceEventRead.model_validate(event) for event in events]


@router.patch(
    "/reviews/{review_id}",
    response_model=ReviewRead,
    dependencies=[Depends(_require_permission("page:reviews"))],
)
async def update_review_record(review_id: UUID, payload: ReviewUpdate, db: DbSession) -> ReviewRead:
    """Update a review record for internal admin correction."""

    review = await _get_or_404(db, Review, review_id, "Review")
    return await _update(db, review, payload, ReviewRead)


@router.delete(
    "/reviews/{review_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_permission("page:reviews"))],
)
async def delete_review_record(review_id: UUID, db: DbSession) -> None:
    """Delete one review record."""

    await _delete(db, Review, review_id, "Review")


def _review_to_read(review: Review) -> ReviewRead:
    """构建带项目名与使用规则的 ReviewRead。

    - ``project_name`` 从 ``review.project.name`` 读（relationship lazy=selectin）；
    - ``rules_used`` 从 ``review.findings`` 聚合 ``rule_id`` 并去重，保持首次出现顺序。

    Review.project 为非空外键（ondelete=CASCADE），故 project 关系必存在，无需 None 兜底。
    """

    read = ReviewRead.model_validate(review)
    read.project_name = review.project.name
    rules_used: list[str] = []
    seen: set[str] = set()
    for finding in review.findings:
        if finding.rule_id not in seen:
            seen.add(finding.rule_id)
            rules_used.append(finding.rule_id)
    read.rules_used = rules_used
    return read
