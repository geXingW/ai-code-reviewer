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
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict

from core import db
from engines import load_builtin_engines
from engines.registry import get_engine_registry
from models.project import Project
from repositories.project import ProjectRepository
from services.gitlab_client_factory import build_gitlab_client_for_project
from services.notification_service import NotificationService
from services.review_orchestrator import (
    GitLabCommitEvent,
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


@dataclass(frozen=True)
class _PushEventInfo:
    """Push Hook 归一化结果（只保留逐 commit 审查需要的字段）。

    Attributes:
        project_id: 数值型 GitLab 项目 ID。
        project_path: 带命名空间的项目路径。
        branch: push 目标分支名（``ref`` 去掉 ``refs/heads/`` 前缀）。
        commits: ``[{"id": sha, "title": ..., "message": ...}, ...]``，
            保持 payload 的时间序（旧 -> 新）。
        pusher_username: 触发 push 的用户名；缺失时 ``None``。
        pusher_name: 同上，显示名。
    """

    project_id: int
    project_path: str
    branch: str
    commits: list[dict[str, Any]]
    pusher_username: str | None
    pusher_name: str | None


@router.post("/gitlab", status_code=status.HTTP_202_ACCEPTED, response_model=GitLabWebhookResponse)
async def handle_gitlab_webhook(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
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

    if x_gitlab_event == "Merge Request Hook" and payload.get("object_kind") == "merge_request":
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

    if x_gitlab_event == "Push Hook" and payload.get("object_kind") == "push":
        return _handle_push_hook(payload, project=project, background_tasks=background_tasks)

    return GitLabWebhookResponse(processed=False, reason="ignored_event")


def _handle_push_hook(
    payload: dict[str, Any],
    *,
    project: Project,
    background_tasks: BackgroundTasks,
) -> GitLabWebhookResponse:
    """Push Hook 过滤链 + 后台调度（逐 commit 审查）。

    过滤链顺序：
      1. ``settings.commit_review_enabled=False`` -> processed=False
         reason=commit_review_disabled（全局止血开关）；
      2. ref 非 ``refs/heads/*``（tag push 等）-> ignored_event；
      3. after 为 40 个 0（删分支）-> ignored_event；
      4. commits 空 -> ignored_event。

    通过后按 ``commit_review_max_per_push`` 截断只审最近 N 个（超出 log
    warning），调度后台任务并**立即返回 202** -- 一次 push N 个 commit 就是
    N 次 LLM 调用，同步等待必然超时。
    """

    from core.config import get_settings

    settings = get_settings()
    if not settings.commit_review_enabled:
        return GitLabWebhookResponse(processed=False, reason="commit_review_disabled")

    push_info = _parse_push_event(payload)
    if push_info is None:
        return GitLabWebhookResponse(processed=False, reason="ignored_event")

    max_per_push = settings.commit_review_max_per_push
    commits = push_info.commits
    if len(commits) > max_per_push:
        dropped = len(commits) - max_per_push
        logger.warning(
            "push carries %d commits; only reviewing the latest %d (dropped %d)",
            len(commits),
            max_per_push,
            dropped,
            extra={
                "gitlab_project_id": push_info.project_id,
                "branch": push_info.branch,
            },
        )
        commits = commits[-max_per_push:]

    scheduled_info = _PushEventInfo(
        project_id=push_info.project_id,
        project_path=push_info.project_path,
        branch=push_info.branch,
        commits=commits,
        pusher_username=push_info.pusher_username,
        pusher_name=push_info.pusher_name,
    )
    background_tasks.add_task(_process_push_commits, scheduled_info, project)
    return GitLabWebhookResponse(
        processed=True,
        status="scheduled",
        reason="push_review_scheduled",
    )


def _parse_push_event(payload: dict[str, Any]) -> _PushEventInfo | None:
    """归一化 Push Hook payload。

    Returns:
        归一化结果；ref 非 ``refs/heads/*``、after 为 40 个 0（删分支）或
        commits 为空时返回 ``None``（调用方按 ignored_event 处理）。
    """

    try:
        project = _expect_dict(payload["project"], "project")
        project_id = int(project["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid GitLab push payload: {exc}",
        ) from exc

    ref = str(payload.get("ref") or "")
    if not ref.startswith("refs/heads/"):
        return None
    after = str(payload.get("after") or "")
    if after == "0" * 40:
        # 删分支 push。
        return None
    raw_commits = payload.get("commits")
    if not isinstance(raw_commits, list) or not raw_commits:
        return None
    commits: list[dict[str, Any]] = []
    for item in raw_commits:
        if not isinstance(item, Mapping):
            continue
        commits.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "message": str(item.get("message") or ""),
            }
        )
    if not commits:
        return None

    user = payload.get("user")
    user_dict = user if isinstance(user, Mapping) else {}
    return _PushEventInfo(
        project_id=project_id,
        project_path=str(project.get("path_with_namespace") or project.get("path") or project_id),
        branch=ref.removeprefix("refs/heads/"),
        commits=commits,
        pusher_username=str(user_dict.get("username") or "") or None,
        pusher_name=str(user_dict.get("name") or "") or None,
    )


async def _process_push_commits(push_info: _PushEventInfo, project: Project) -> None:
    """后台逐 commit 串行审查（单个失败 except + log 后继续下一个）。

    本期不做钉钉推送（每 commit 一条会刷屏），notification_service 传 None。
    """

    load_builtin_engines()
    client = build_gitlab_client_for_project(project)

    from core.config import get_settings

    settings = get_settings()
    orchestrator = ReviewOrchestrator(
        gitlab_client=client,
        engine_registry=get_engine_registry(),
        default_engine=settings.default_review_engine,
        session_factory=db.AsyncSessionLocal,
        notification_service=None,
    )
    for commit in push_info.commits:
        commit_sha = str(commit.get("id") or "")
        if not commit_sha:
            continue
        event = GitLabCommitEvent(
            project_id=push_info.project_id,
            project_path=push_info.project_path,
            commit_sha=commit_sha,
            branch=push_info.branch,
            title=str(commit.get("title") or ""),
            message=str(commit.get("message") or ""),
            author_username=push_info.pusher_username,
            author_name=push_info.pusher_name,
        )
        try:
            await orchestrator.review_commit(event)
        except Exception:
            logger.exception(
                "commit review failed; continuing with next commit",
                extra={
                    "gitlab_project_id": push_info.project_id,
                    "commit_sha": commit_sha,
                    "branch": push_info.branch,
                },
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

    MR 作者信息取顶层 ``user`` 对象（触发事件的用户）：open 事件中即为 MR
    创建人；update 事件中是触发者，@ 触发者同样是合理的通知对象。字段缺失
    时留 ``None``，通知侧 fail-silent 不 @ 人。

    Raises:
        HTTPException: If required fields are absent or invalid.
    """

    try:
        project = _expect_dict(payload["project"], "project")
        attrs = _expect_dict(payload["object_attributes"], "object_attributes")
        last_commit = _expect_dict(attrs.get("last_commit", {}), "last_commit")
        target = _expect_dict(attrs.get("target", {}), "target")
        user = _expect_dict(payload.get("user", {}), "user")
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
        created_at=str(attrs.get("created_at") or ""),
        author_username=str(user.get("username") or "") or None,
        author_name=str(user.get("name") or "") or None,
    )


def _expect_dict(value: object, field_name: str) -> dict[str, Any]:
    """Return ``value`` if it is a dict-like mapping, otherwise raise TypeError."""

    if not isinstance(value, Mapping):
        msg = f"{field_name} must be an object"
        raise TypeError(msg)
    return dict(value)
