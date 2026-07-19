"""Tests for GitLab webhook ingestion endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.api import gitlab_webhook
from app.services.review_orchestrator import GitLabMergeRequestEvent, OrchestratorResult


@pytest.mark.asyncio
async def test_gitlab_webhook_rejects_invalid_secret(client: AsyncClient) -> None:
    """Webhook endpoint enforces X-Gitlab-Token when configured."""

    response = await client.post(
        "/api/webhooks/gitlab",
        headers={"X-Gitlab-Event": "Merge Request Hook", "X-Gitlab-Token": "wrong"},
        json={"object_kind": "merge_request"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_gitlab_webhook_ignores_non_merge_request_events(client: AsyncClient) -> None:
    """Only MR hooks are processed by the MVP endpoint."""

    response = await client.post(
        "/api/webhooks/gitlab",
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "test-webhook-secret"},
        json={"object_kind": "push"},
    )

    assert response.status_code == 202
    assert response.json()["processed"] is False


@pytest.mark.asyncio
async def test_gitlab_webhook_dispatches_supported_merge_request(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Supported MR actions are parsed and dispatched to orchestrator."""

    captured: dict[str, GitLabMergeRequestEvent] = {}

    async def fake_review(event: GitLabMergeRequestEvent) -> OrchestratorResult:
        captured["event"] = event
        return OrchestratorResult(
            review_id=None,
            project_uuid=event.project_uuid,
            status="done",
            finding_count=0,
            has_blocker=False,
            note_id=1,
        )

    monkeypatch.setattr(gitlab_webhook, "review_merge_request_event", fake_review)

    response = await client.post(
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
    assert event.project_id == 123
    assert event.mr_iid == 7
    assert event.source_commit_sha == "abc123"


@pytest.mark.asyncio
async def test_gitlab_webhook_dispatches_mr_close_action(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MR ``close`` action 被视作合法的生命周期事件（不再走 ignored_action 分支）。

    orchestrator 层的具体动作（批量翻 finding 状态、记账 review）在
    ``test_mr_lifecycle.py`` 里覆盖；这里只验证 webhook 到分派边界的握手。
    """

    captured: dict[str, GitLabMergeRequestEvent] = {}

    async def fake_review(event: GitLabMergeRequestEvent) -> OrchestratorResult:
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

    response = await client.post(
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
