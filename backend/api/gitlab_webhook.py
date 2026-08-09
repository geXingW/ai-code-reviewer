"""GitLab webhook ingestion endpoints.

The endpoint handles GitLab Merge Request Hook events, validates the shared
secret at the project level, normalizes the payload into a service-layer event,
and dispatches the review orchestrator. The current MVP processes inline so the
GitLab integration can be exercised end-to-end; a later queue worker can move
the same service call out of the request path.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict

from core import db
from engines import load_builtin_engines
from engines.registry import get_engine_registry
from models.project import Project
from repositories.project import ProjectRepository
from services.gitlab_client_factory import build_gitlab_client_for_project
from services.notification_service import NotificationService
from services.review_orchestrator import (
    GitLabMergeRequestEvent,
    OrchestratorResult,
    ReviewOrchestrator,
    SessionFactory,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
_SUPPORTED_ACTIONS = {"open", "reopen", "update", "close", "merge"}


class GitLabWebhookResponse(BaseModel):
    """Response returned by the GitLab webhook endpoint."""

    model_config = ConfigDict(extra="forbid")

    processed: bool
    reason: str | None = None
    status: str | None = None
    finding_count: int | None = None
    has_blocker: bool | None = None
    note_id: int | None = None


@router.post("/gitlab", status_code=status.HTTP_202_ACCEPTED, response_model=GitLabWebhookResponse)
async def handle_gitlab_webhook(
    payload: dict[str, Any],
    x_gitlab_event: str | None = Header(default=None, alias="X-Gitlab-Event"),
    x_gitlab_token: str | None = Header(default=None, alias="X-Gitlab-Token"),
) -> GitLabWebhookResponse:
    """Accept and process GitLab merge request webhook events.

    Args:
        payload: Raw GitLab webhook JSON payload.
        x_gitlab_event: GitLab event type header.
        x_gitlab_token: Shared secret header configured on the webhook.

    Returns:
        GitLabWebhookResponse: Processing summary.
    """

    # 1. 轻量解析 project_id，再查 DB 拿项目（不泄露项目存在性）。
    project_id = _extract_project_id(payload)
    project = await _resolve_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook token",
        )

    # 2. 项目 disabled → 202 + processed=false。
    if not project.enabled:
        return GitLabWebhookResponse(processed=False, reason="project_disabled")

    # 3. 用项目级 webhook_secret 校验 X-Gitlab-Token。
    _validate_webhook_secret(x_gitlab_token, project)

    if x_gitlab_event != "Merge Request Hook" or payload.get("object_kind") != "merge_request":
        return GitLabWebhookResponse(processed=False, reason="ignored_event")

    # 4. 完整解析 MR event。
    event = _parse_merge_request_event(payload)
    if event.action not in _SUPPORTED_ACTIONS:
        return GitLabWebhookResponse(processed=False, reason="ignored_action")

    # 5. 调用 review 流程，传入 project 以便构造项目级 GitLabClient。
    result = await review_merge_request_event(event, project=project)
    return GitLabWebhookResponse(
        processed=True,
        status=result.status,
        finding_count=result.finding_count,
        has_blocker=result.has_blocker,
        note_id=result.note_id,
    )


async def review_merge_request_event(
    event: GitLabMergeRequestEvent,
    *,
    project: Project | None = None,
    session_factory: SessionFactory | None = None,
) -> OrchestratorResult:
    """Build runtime dependencies and run the MR review orchestrator.

    Args:
        event: 规范化后的 GitLab MR 事件。
        project: 对应的 Project 记录。若为 None 则通过 gitlab_project_id 查库。
        session_factory: 可选的 sessionmaker 覆盖。测试里可传入 test_engine
            对应的 factory，避免复用模块级 ``AsyncSessionLocal`` 绑到已关闭的 loop。
    """

    # 兼容旧调用：未传 project 时，从事件的 gitlab_project_id 反查。
    if project is None:
        effective_session_factory = session_factory or db.AsyncSessionLocal
        async with effective_session_factory() as session:
            repo = ProjectRepository(session)
            project = await repo.get_by_gitlab_project_id(str(event.project_id))
            if project is None:
                msg = f"No project found for gitlab_project_id={event.project_id}"
                raise ValueError(msg)

    load_builtin_engines()
    client = build_gitlab_client_for_project(project)

    from core.config import get_settings

    settings = get_settings()

    # 注入应用级 sessionmaker，让 orchestrator 每次评审完成后能落库 review + finding。
    effective_session_factory = session_factory or db.AsyncSessionLocal
    orchestrator = ReviewOrchestrator(
        gitlab_client=client,
        engine_registry=get_engine_registry(),
        default_engine=settings.default_review_engine,
        session_factory=effective_session_factory,
        # 通知服务复用同一 sessionmaker，按项目渠道推送 Review 完成结果。
        notification_service=NotificationService(effective_session_factory),
    )
    return await orchestrator.review_merge_request(event)


def _extract_project_id(payload: dict[str, Any]) -> int:
    """Lightweight extraction of ``project.id`` from a webhook payload.

    Only parses the minimum needed to identify the target project. Full event
    validation happens later in :func:`_parse_merge_request_event`.

    Raises:
        HTTPException: 422 if the project.id field is missing or invalid.
    """

    try:
        project = _expect_dict(payload["project"], "project")
        project_id = int(project["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid GitLab merge request payload: {exc}",
        ) from exc
    return project_id


async def _resolve_project(project_id: int) -> Project | None:
    """Look up a Project by its GitLab numeric project ID.

    Returns None if no matching project is found (caller should return 401
    to avoid leaking existence).
    """

    async with db.AsyncSessionLocal() as session:
        repo = ProjectRepository(session)
        return await repo.get_by_gitlab_project_id(str(project_id))


def _validate_webhook_secret(token: str | None, project: Project) -> None:
    """Validate GitLab webhook token using constant-time comparison.

    Uses ``project.webhook_secret`` as the expected value.
    """

    expected = project.webhook_secret
    if not expected:
        logger.warning(
            "GitLab webhook secret is empty for project %s; rejecting request for safety",
            project.gitlab_project_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook token",
        )
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook token",
        )


def _parse_merge_request_event(payload: dict[str, Any]) -> GitLabMergeRequestEvent:
    """Normalize a raw GitLab MR webhook payload.

    Raises:
        HTTPException: If required fields are absent or invalid.
    """

    try:
        project = _expect_dict(payload["project"], "project")
        attrs = _expect_dict(payload["object_attributes"], "object_attributes")
        last_commit = _expect_dict(attrs.get("last_commit", {}), "last_commit")
        target = _expect_dict(attrs.get("target", {}), "target")
        project_id = int(project["id"])
        mr_iid = int(attrs["iid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid GitLab merge request payload: {exc}",
        ) from exc

    source_commit_sha = str(last_commit.get("id") or attrs.get("last_commit_sha") or "")
    if not source_commit_sha:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid GitLab merge request payload: missing last commit id",
        )

    return GitLabMergeRequestEvent(
        project_id=project_id,
        project_path=str(project.get("path_with_namespace") or project.get("path") or project_id),
        mr_iid=mr_iid,
        source_branch=str(attrs.get("source_branch") or ""),
        target_branch=str(attrs.get("target_branch") or target.get("default_branch") or ""),
        source_commit_sha=source_commit_sha,
        target_commit_sha=str(attrs.get("target_branch_sha") or target.get("default_branch") or ""),
        action=str(attrs.get("action") or ""),
        title=str(attrs.get("title") or ""),
        web_url=str(attrs.get("url") or "") or None,
        description=str(attrs.get("description") or ""),
        last_commit_message=str(last_commit.get("message") or ""),
    )


def _expect_dict(value: object, field_name: str) -> dict[str, Any]:
    """Return ``value`` if it is a dict-like mapping, otherwise raise TypeError."""

    if not isinstance(value, Mapping):
        msg = f"{field_name} must be an object"
        raise TypeError(msg)
    return dict(value)
