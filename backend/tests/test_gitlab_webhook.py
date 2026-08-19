"""Tests for GitLab webhook ingestion endpoint."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from api import gitlab_webhook
from api.gitlab_webhook import _PushEventInfo
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


# ---------------------------------------------------------------------------
# Push Hook（commit 级审查入口）
# ---------------------------------------------------------------------------


def _push_payload(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造最小可用的 Push Hook payload。"""

    payload: dict[str, Any] = {
        "object_kind": "push",
        "before": "old" * 10,
        "after": "new" * 10,
        "ref": "refs/heads/feature/x",
        "project": {"id": 123, "path_with_namespace": "group/demo"},
        "user": {"username": "alice", "name": "Alice Zhang"},
        "commits": [{"id": "sha-1", "title": "first", "message": "first"}],
    }
    payload.update(overrides or {})
    return payload


def _patch_push_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[_PushEventInfo, Project]]:
    """把后台任务替换成捕获调用参数的假实现，避免真实 GitLab 调用。"""

    captured: list[tuple[_PushEventInfo, Project]] = []

    async def fake_processor(push_info: _PushEventInfo, project: Project) -> None:
        captured.append((push_info, project))

    monkeypatch.setattr(gitlab_webhook, "_process_push_commits", fake_processor)
    return captured


@pytest.mark.asyncio
async def test_push_hook_returns_202_and_schedules_background_task(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Push Hook -> 立即 202 + processed=True + 后台任务已调度并执行。"""

    project = await _create_test_project(db_session_factory)
    captured = _patch_push_processor(monkeypatch)

    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "test-webhook-secret"},
        json=_push_payload(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["processed"] is True
    assert body["status"] == "scheduled"
    assert body["reason"] == "push_review_scheduled"
    # ASGITransport 在响应后执行 background tasks -> 捕获到 (push_info, project)。
    assert len(captured) == 1
    push_info, captured_project = captured[0]
    assert push_info.project_id == 123
    assert push_info.branch == "feature/x"
    assert [c["id"] for c in push_info.commits] == ["sha-1"]
    assert captured_project.id == project.id


@pytest.mark.asyncio
async def test_push_hook_truncates_to_latest_k_commits(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """21 个 commit 的 push -> 只调度最近 10 个（默认 max_per_push）。"""

    await _create_test_project(db_session_factory)
    captured = _patch_push_processor(monkeypatch)

    commits = [
        {"id": f"sha-{i:02d}", "title": f"c{i}", "message": f"c{i}"}
        for i in range(1, 22)
    ]
    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "test-webhook-secret"},
        json=_push_payload({"commits": commits}),
    )

    assert response.status_code == 202
    assert response.json()["processed"] is True
    assert len(captured) == 1
    scheduled_ids = [c["id"] for c in captured[0][0].commits]
    # 保留最新的 10 条（sha-12..sha-21），时间序保持旧 -> 新。
    assert scheduled_ids == [f"sha-{i:02d}" for i in range(12, 22)]


@pytest.mark.asyncio
async def test_push_hook_disabled_returns_commit_review_disabled(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit_review_enabled=False -> processed=False reason=commit_review_disabled。"""

    from core import config

    await _create_test_project(db_session_factory)
    _patch_push_processor(monkeypatch)
    # 走真实 Settings（env 驱动）而不是替身：conftest teardown 会调
    # get_settings.cache_clear，替身 lambda 没有该属性会把 teardown 炸掉。
    monkeypatch.setenv("COMMIT_REVIEW_ENABLED", "false")
    config.get_settings.cache_clear()

    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "test-webhook-secret"},
        json=_push_payload(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["processed"] is False
    assert body["reason"] == "commit_review_disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "label"),
    [
        ({"after": "0" * 40}, "branch deletion"),
        ({"ref": "refs/tags/v1.0.0"}, "non-heads ref"),
        ({"commits": []}, "empty commits"),
    ],
)
async def test_push_hook_ignores_invalid_pushes(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    label: str,
) -> None:
    """删分支 / 非 heads ref / commits 空 -> ignored_event，不调度后台任务。"""

    await _create_test_project(db_session_factory)
    captured = _patch_push_processor(monkeypatch)

    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "test-webhook-secret"},
        json=_push_payload(overrides),
    )

    assert response.status_code == 202, label
    body = response.json()
    assert body["processed"] is False, label
    assert body["reason"] == "ignored_event", label
    assert captured == [], label


@pytest.mark.asyncio
async def test_push_hook_still_validates_project_secret(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """Push Hook 同样走项目解析 + secret 校验（分发之前，不重复实现）。"""

    await _create_test_project(db_session_factory)

    response = await db_client.post(
        "/api/webhooks/gitlab",
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "wrong"},
        json=_push_payload(),
    )

    assert response.status_code == 401
