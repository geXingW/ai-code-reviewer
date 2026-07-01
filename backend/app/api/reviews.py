"""Synchronous review trigger API used by Jenkins pipelines."""

from __future__ import annotations

import hmac
import logging
from collections import deque
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.api.gitlab_webhook import review_merge_request_event
from app.core.config import get_settings
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


_recent_reviews: deque[RecentReviewRead] = deque(maxlen=20)


@router.get("/recent", response_model=list[RecentReviewRead])
async def list_recent_reviews(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> list[RecentReviewRead]:
    """Return the latest in-memory review summaries for the MVP dashboard."""

    _validate_internal_token(x_internal_token)
    return list(_recent_reviews)


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
