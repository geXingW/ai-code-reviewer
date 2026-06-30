"""Synchronous review trigger API used by Jenkins pipelines."""

from __future__ import annotations

import hmac
import logging
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

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
    web_url: str | None = Field(
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
        web_url=payload.web_url,
    )
    result = await review_merge_request_event(event)
    return ReviewCreateResponse(
        review_id=result.review_id,
        status=result.status,
        has_blocker=result.has_blocker,
        finding_count=result.finding_count,
        blocker_count=result.blocker_count,
        policy_applied=result.policy_applied,
        review_url=_build_review_url(
            web_url=payload.web_url,
            note_id=result.note_id,
            review_id=result.review_id,
        ),
    )


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
