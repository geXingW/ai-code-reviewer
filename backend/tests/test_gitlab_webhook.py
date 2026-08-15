"""Tests for GitLab webhook ingestion endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from api import gitlab_webhook
from models.project import Project
from services.review_orchestrator import GitLabMergeRequestEvent, OrchestratorResult


async def _create_test_project(
    session_factory: async_sessionmaker,
    *,
    gitlab_project_id: str = "123",
    webhook_secret: str = "test-webhook-secret",
    gitlab_base_url: str = "https://gitlab.example.com",
    gitlab_access_token: str = "glpat-test",
    enabled: bool = True,
) -> Project:
    """Create a Project record directly for webhook tests."""

    project = Project(
        name="test-project",
        gitlab_project_id=gitlab_project_id,
        gitlab_base_url=gitlab_base_url,
        gitlab_access_token=gitlab_access_token,
        webhook_secret=webhook_secret,
        enabled=enabled,
    )
    async with session_factory() as session:
        session.add(project)
        await session.commit()
        await session.refresh(project)
    return project


@pytest.mark.asyncio
async def test_gitlab_webhook_rejects_invalid_secret(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """Webhook endpoint rejects wrong secret with 401 when project exists."""

    await _create_test_project(db_session_factory)

    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={"X-Gitlab-Event": "Merge Request Hook", "X-Gitlab-Token": "wrong"},
        json={"object_kind": "merge_request", "project": {"id": 123}},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_gitlab_webhook_rejects_unknown_project(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """Webhook returns 401 for unknown project IDs (no existence leak)."""

    # 不建项目，直接调用 → 应返回 401，与 secret 错误同语义。
    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={"X-Gitlab-Event": "Merge Request Hook", "X-Gitlab-Token": "whatever"},
        json={"object_kind": "merge_request", "project": {"id": 9999}},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook token"


@pytest.mark.asyncio
async def test_gitlab_webhook_returns_202_for_disabled_project(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """Disabled projects return 202 with processed=false, reason=project_disabled."""

    await _create_test_project(db_session_factory, enabled=False)

    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={
            "X-Gitlab-Event": "Merge Request Hook",
            "X-Gitlab-Token": "test-webhook-secret",
        },
        json={"object_kind": "merge_request", "project": {"id": 123}},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["processed"] is False
    assert body["reason"] == "project_disabled"


@pytest.mark.asyncio
async def test_gitlab_webhook_ignores_non_merge_request_events(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """Only MR hooks are processed by the MVP endpoint."""

    await _create_test_project(db_session_factory)

    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "test-webhook-secret"},
        json={"object_kind": "push", "project": {"id": 123}},
    )

    assert response.status_code == 202
    assert response.json()["processed"] is False


@pytest.mark.asyncio
async def test_gitlab_webhook_dispatches_supported_merge_request(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Supported MR actions are parsed and dispatched to orchestrator."""

    await _create_test_project(db_session_factory)

    captured: dict[str, object] = {}

    async def fake_review(
        event: GitLabMergeRequestEvent,
        *args: object,
        **kwargs: object,
    ) -> OrchestratorResult:
        captured["event"] = event
        captured["project"] = kwargs.get("project")
        return OrchestratorResult(
            review_id=None,
            project_uuid=event.project_uuid,
            status="done",
            finding_count=0,
            has_blocker=False,
            note_id=1,
        )

    monkeypatch.setattr(gitlab_webhook, "review_merge_request_event", fake_review)

    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={
            "X-Gitlab-Event": "Merge Request Hook",
            "X-Gitlab-Token": "test-webhook-secret",
        },
        json={
            "object_kind": "merge_request",
            "project": {"id": 123, "path_with_namespace": "group/demo"},
            "object_attributes": {
                "iid": 7,
                "action": "open",
                "source_branch": "feature/demo",
                "target_branch": "master",
                "last_commit": {"id": "abc123"},
                "target": {"default_branch": "master"},
                "title": "Demo MR",
                "url": "https://gitlab.example.com/group/demo/-/merge_requests/7",
            },
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["processed"] is True
    assert body["status"] == "done"
    event = captured["event"]
    assert isinstance(event, GitLabMergeRequestEvent)
    assert event.project_id == 123
    assert event.mr_iid == 7
    assert event.source_commit_sha == "abc123"


@pytest.mark.asyncio
async def test_gitlab_webhook_dispatches_mr_close_action(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MR ``close`` action 被视作合法的生命周期事件（不再走 ignored_action 分支）。

    orchestrator 层的具体动作（批量翻 finding 状态、记账 review）在
    ``test_mr_lifecycle.py`` 里覆盖；这里只验证 webhook 到分派边界的握手。
    """

    await _create_test_project(db_session_factory)

    captured: dict[str, GitLabMergeRequestEvent] = {}

    async def fake_review(
        event: GitLabMergeRequestEvent,
        *args: object,
        **kwargs: object,
    ) -> OrchestratorResult:
        captured["event"] = event
        return OrchestratorResult(
            review_id=None,
            project_uuid=event.project_uuid,
            status="done",
            finding_count=0,
            has_blocker=False,
            note_id=None,
        )

    monkeypatch.setattr(gitlab_webhook, "review_merge_request_event", fake_review)

    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={
            "X-Gitlab-Event": "Merge Request Hook",
            "X-Gitlab-Token": "test-webhook-secret",
        },
        json={
            "object_kind": "merge_request",
            "project": {"id": 123, "path_with_namespace": "group/demo"},
            "object_attributes": {
                "iid": 7,
                "action": "close",
                "source_branch": "feature/demo",
                "target_branch": "master",
                "last_commit": {"id": "abc123"},
                "target": {"default_branch": "master"},
                "title": "Demo MR",
                "url": "https://gitlab.example.com/group/demo/-/merge_requests/7",
            },
        },
    )

    assert response.status_code == 202
    body = response.json()
    # close action 现在也算 supported —— processed=True 且到达 orchestrator。
    assert body["processed"] is True
    assert body["status"] == "done"
    assert captured["event"].action == "close"


@pytest.mark.asyncio
async def test_gitlab_webhook_returns_422_when_payload_lacks_project_id(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """Payload 缺 project.id 时返回 422（解析失败）。"""

    await _create_test_project(db_session_factory)

    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={
            "X-Gitlab-Event": "Merge Request Hook",
            "X-Gitlab-Token": "test-webhook-secret",
        },
        json={"object_kind": "merge_request"},
    )

    assert response.status_code == 422


def _minimal_mr_payload() -> dict[str, object]:
    """构造能通过 ``_parse_merge_request_event`` 校验的最小 MR payload。"""

    return {
        "object_kind": "merge_request",
        "project": {"id": 123, "path_with_namespace": "group/demo"},
        "object_attributes": {
            "iid": 7,
            "action": "open",
            "source_branch": "feature/demo",
            "target_branch": "master",
            "last_commit": {"id": "abc123"},
            "target": {"default_branch": "master"},
            "title": "Demo MR",
            "url": "https://gitlab.example.com/group/demo/-/merge_requests/7",
        },
    }


def test_parse_merge_request_event_extracts_author_from_user() -> None:
    """顶层 ``user`` 对象解析为 MR 作者信息（open 事件中即创建人）。"""

    payload = _minimal_mr_payload()
    payload["user"] = {"username": "alice", "name": "Alice Zhang"}

    event = gitlab_webhook._parse_merge_request_event(payload)

    assert event.author_username == "alice"
    assert event.author_name == "Alice Zhang"


def test_parse_merge_request_event_without_user_leaves_author_none() -> None:
    """payload 无 ``user`` 对象时作者信息留空（通知侧不 @ 人）。"""

    event = gitlab_webhook._parse_merge_request_event(_minimal_mr_payload())

    assert event.author_username is None
    assert event.author_name is None
