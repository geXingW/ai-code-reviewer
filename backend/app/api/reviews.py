"""Synchronous review trigger API used by Jenkins pipelines."""

from __future__ import annotations

import hmac
import logging
from collections import deque
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.api.gitlab_webhook import review_merge_request_event
from app.core.config import get_settings
from app.core.db import DbSession
from app.repositories.review import ReviewRepository
from app.schemas._datetime import AwareDatetime
from app.services.review_orchestrator import GitLabMergeRequestEvent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reviews", tags=["reviews"])


class ReviewCreateRequest(BaseModel):
    """Jenkins synchronous review trigger payload."""

    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0, description="Numeric GitLab project ID.")
    mr_iid: int = Field(gt=0, description="Merge request IID scoped to the GitLab project.")
    target_branch: str = Field(min_length=1, description="Target branch under review.")
    source_branch: str = Field(min_length=1, description="Source branch under review.")
    commit_sha: str = Field(min_length=1, description="MR head commit SHA to review.")
    target_commit_sha: str | None = Field(default=None, description="Best-known target/base SHA.")
    project_path: str | None = Field(default=None, description="Namespace-qualified GitLab path.")
    title: str | None = Field(default=None, description="Merge request title.")
    web_url: AnyHttpUrl | None = Field(
        default=None,
        description="Browser URL of the GitLab merge request.",
    )


class ReviewCreateResponse(BaseModel):
    """Jenkins-facing synchronous review result."""

    model_config = ConfigDict(extra="forbid")

    review_id: UUID | None
    status: str
    has_blocker: bool
    finding_count: int
    blocker_count: int
    policy_applied: str | None
    review_url: str | None


class RecentReviewRead(BaseModel):
    """Sanitized review summary shown by the MVP dashboard."""

    model_config = ConfigDict(extra="forbid")

    review_id: UUID | None
    project_id: int
    project_path: str
    mr_iid: int
    title: str
    web_url: str | None
    status: str
    has_blocker: bool
    finding_count: int
    blocker_count: int
    policy_applied: str | None
    review_url: str | None
    # Issue #76：新增引擎与创建时间，前端展示"何时用哪个引擎评的"。
    engine_used: str | None = None
    created_at: AwareDatetime | None = None
    # PR #89：让首页最近评审面板也能看到"全量 / 增量"徽章。
    # DB 里 review_mode 为 NOT NULL server_default='full'，但对内存 deque 兜底
    # （见 _record_recent_review）也可能没设置，这里保留 Optional 并给默认 'full'。
    review_mode: str | None = "full"
    # PR #96：MR 生命周期事件记账 Review 的标签（mr_closed / mr_merged）。
    # 普通审查为 None；前端有值时优先渲染专属徽章，替代 review_mode 徽章。
    lifecycle_event: str | None = None


_recent_reviews: deque[RecentReviewRead] = deque(maxlen=20)


@router.get("/recent", response_model=list[RecentReviewRead])
async def list_recent_reviews(
    db: DbSession,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> list[RecentReviewRead]:
    """从 DB 读最近 20 条评审用于首页最近审查面板。

    历史实现仅在 ``POST /api/reviews`` 时把摘要追加到内存 deque，webhook
    路径不经过该接口，首页永远看不到数据。本实现改为直接查库，DB 查询
    失败时回退到旧 deque 保持退化可用。
    """

    _validate_internal_token(x_internal_token)
    try:
        reviews_repo = ReviewRepository(db)
        rows = await reviews_repo.list_recent(limit=20)
    except (SQLAlchemyError, OSError, ConnectionError) as exc:
        # 涵盖 SQLAlchemy 抛出的所有 DB 层错误，以及 asyncpg 连接期未被包装的
        # 原生错误（OSError / ConnectionError），失败时回退到内存 deque 兜底。
        logger.warning("recent reviews DB fallback: %s", exc)
        return list(_recent_reviews)
    except Exception as exc:  # noqa: BLE001 — DB 拉取失败必须降级，避免面板 500。
        logger.warning("recent reviews DB fallback (unexpected): %s", exc)
        return list(_recent_reviews)
    return [_to_recent_review(row) for row in rows]


def _to_recent_review(review: object) -> RecentReviewRead:
    """将 Review ORM 行转换为面板摘要 schema。

    - project_path 直接复用 project.name（前端已展示；比 UUID/数字 ID 更可读）。
    - project_id 尝试从 gitlab_project_id 解析为 int，兜底 0。
    - blocker_count 通过 selectinload 的 findings 在 Python 侧统计，避免 N+1。
    """

    # 使用局部导入避免 typing.TYPE_CHECKING 与 forward ref 环。
    project = getattr(review, "project", None)
    project_name = getattr(project, "name", None) or "-"
    gitlab_project_id_raw = getattr(project, "gitlab_project_id", None) if project else None
    try:
        gitlab_project_id = int(gitlab_project_id_raw) if gitlab_project_id_raw is not None else 0
    except (TypeError, ValueError):
        gitlab_project_id = 0

    findings = getattr(review, "findings", []) or []
    blocker_count = sum(1 for f in findings if getattr(f, "severity", "") == "BLOCKER")

    mr_iid_raw = getattr(review, "mr_iid", "0")
    try:
        mr_iid = int(mr_iid_raw)
    except (TypeError, ValueError):
        mr_iid = 0

    return RecentReviewRead(
        review_id=getattr(review, "id", None),
        project_id=gitlab_project_id,
        project_path=project_name,
        mr_iid=mr_iid,
        title=f"MR !{mr_iid_raw}",
        web_url=None,
        status=getattr(review, "status", "unknown"),
        has_blocker=bool(getattr(review, "has_blocker", False)),
        finding_count=int(getattr(review, "finding_count", 0) or 0),
        blocker_count=blocker_count,
        policy_applied=None,
        review_url=None,
        engine_used=getattr(review, "engine_used", None),
        created_at=getattr(review, "created_at", None),
        # PR #89：DB 里 review_mode 是 NOT NULL server_default='full'，老数据
        # 迁移后必然有值。这里读不到就兜底 'full'，防前端徽章渲染出错。
        review_mode=getattr(review, "review_mode", None) or "full",
        # PR #96：普通审查该列为 NULL；close/merge webhook 记账 review 才有值。
        lifecycle_event=getattr(review, "lifecycle_event", None),
    )


@router.post("", response_model=ReviewCreateResponse)
async def create_review(
    payload: ReviewCreateRequest,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> ReviewCreateResponse:
    """Synchronously run one MR review and return blocker summary for Jenkins.

    Webhook handlers can return quickly, but Jenkins needs a deterministic JSON
    response so the pipeline can decide whether to fail the build. This endpoint
    therefore runs the same orchestrator inline and exposes only stable summary
    fields required by CI.
    """

    _validate_internal_token(x_internal_token)
    web_url = str(payload.web_url) if payload.web_url is not None else None
    event = GitLabMergeRequestEvent(
        project_id=payload.project_id,
        project_path=payload.project_path or str(payload.project_id),
        mr_iid=payload.mr_iid,
        source_branch=payload.source_branch,
        target_branch=payload.target_branch,
        source_commit_sha=payload.commit_sha,
        target_commit_sha=payload.target_commit_sha or "",
        action="jenkins_sync",
        title=payload.title or "",
        web_url=web_url,
        description="",
        last_commit_message="",
    )
    result = await review_merge_request_event(event)
    review_url = _build_review_url(
        web_url=web_url,
        note_id=result.note_id,
        review_id=result.review_id,
    )
    response = ReviewCreateResponse(
        review_id=result.review_id,
        status=result.status,
        has_blocker=result.has_blocker,
        finding_count=result.finding_count,
        blocker_count=result.blocker_count,
        policy_applied=result.policy_applied,
        review_url=review_url,
    )
    _record_recent_review(payload=payload, result=response)
    return response


def _validate_internal_token(token: str | None) -> None:
    """Validate Jenkins internal token using constant-time comparison."""

    expected = get_settings().internal_api_token.get_secret_value()
    if not expected:
        logger.warning("Internal API token is empty; rejecting request for safety")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )


def _build_review_url(
    *,
    web_url: str | None,
    note_id: int | None,
    review_id: UUID | None,
) -> str | None:
    """Build a human-friendly URL to the GitLab note or local review fallback."""

    if web_url and note_id is not None:
        return f"{web_url}#note_{note_id}"
    if review_id is not None:
        return f"/api/reviews/{review_id}"
    return web_url


def _record_recent_review(*, payload: ReviewCreateRequest, result: ReviewCreateResponse) -> None:
    """Store a sanitized bounded review summary for the MVP dashboard."""

    _recent_reviews.appendleft(
        RecentReviewRead(
            review_id=result.review_id,
            project_id=payload.project_id,
            project_path=payload.project_path or str(payload.project_id),
            mr_iid=payload.mr_iid,
            title=payload.title or f"MR !{payload.mr_iid}",
            web_url=str(payload.web_url) if payload.web_url is not None else None,
            status=result.status,
            has_blocker=result.has_blocker,
            finding_count=result.finding_count,
            blocker_count=result.blocker_count,
            policy_applied=result.policy_applied,
            review_url=result.review_url,
        )
    )


def clear_recent_reviews_for_tests() -> None:
    """Clear in-memory dashboard data between API tests."""

    _recent_reviews.clear()
