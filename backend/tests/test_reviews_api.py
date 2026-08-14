"""Tests for Jenkins synchronous review trigger API."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient

from api import reviews
from services.review_orchestrator import GitLabMergeRequestEvent, OrchestratorResult


@pytest.mark.asyncio
async def test_create_review_rejects_missing_internal_token(client: AsyncClient) -> None:
    """Jenkins review API requires a server-to-server internal token."""

    response = await client.post(
        "/api/reviews",
        json={
            "project_id": 123,
            "mr_iid": 7,
            "target_branch": "master",
            "source_branch": "feature/demo",
            "commit_sha": "abc123",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid internal token"


@pytest.mark.asyncio
async def test_create_review_runs_orchestrator_and_returns_blocking_summary(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid Jenkins request synchronously returns blocker summary fields."""

    captured: dict[str, GitLabMergeRequestEvent] = {}

    async def fake_review(event: GitLabMergeRequestEvent) -> OrchestratorResult:
        captured["event"] = event
        return OrchestratorResult(
            review_id=UUID("00000000-0000-0000-0000-000000000123"),
            project_uuid=event.project_uuid,
            status="done",
            finding_count=3,
            has_blocker=True,
            blocker_count=2,
            policy_applied="master -> BLOCKER",
            note_id=99,
        )

    monkeypatch.setattr(reviews, "review_merge_request_event", fake_review)

    response = await client.post(
        "/api/reviews",
        headers={"X-Internal-Token": "test-internal-token"},
        json={
            "project_id": 123,
            "project_path": "group/demo",
            "mr_iid": 7,
            "target_branch": "master",
            "source_branch": "feature/demo",
            "commit_sha": "abc123",
            "target_commit_sha": "base456",
            "title": "Demo MR",
            "web_url": "https://gitlab.example.com/group/demo/-/merge_requests/7",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "review_id": "00000000-0000-0000-0000-000000000123",
        "status": "done",
        "has_blocker": True,
        "finding_count": 3,
        "blocker_count": 2,
        "policy_applied": "master -> BLOCKER",
        "review_url": "https://gitlab.example.com/group/demo/-/merge_requests/7#note_99",
    }
    event = captured["event"]
    assert event.project_id == 123
    assert event.project_path == "group/demo"
    assert event.mr_iid == 7
    assert event.source_branch == "feature/demo"
    assert event.target_branch == "master"
    assert event.source_commit_sha == "abc123"
    assert event.target_commit_sha == "base456"
    assert event.action == "jenkins_sync"


@pytest.mark.asyncio
async def test_create_review_builds_fallback_review_url(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response still contains a stable review URL when GitLab note ID is absent."""

    async def fake_review(event: GitLabMergeRequestEvent) -> OrchestratorResult:
        return OrchestratorResult(
            review_id=UUID("00000000-0000-0000-0000-000000000456"),
            project_uuid=event.project_uuid,
            status="done",
            finding_count=0,
            has_blocker=False,
            blocker_count=0,
            policy_applied="master -> BLOCKER",
            note_id=None,
        )

    monkeypatch.setattr(reviews, "review_merge_request_event", fake_review)

    response = await client.post(
        "/api/reviews",
        headers={"X-Internal-Token": "test-internal-token"},
        json={
            "project_id": 123,
            "mr_iid": 7,
            "target_branch": "master",
            "source_branch": "feature/demo",
            "commit_sha": "abc123",
        },
    )

    assert response.status_code == 200
    assert response.json()["review_url"] == "/api/reviews/00000000-0000-0000-0000-000000000456"


@pytest.mark.asyncio
async def test_recent_reviews_rejects_missing_auth(client: AsyncClient) -> None:
    """Dashboard recent reviews endpoint 受 admin JWT 保护，缺 token 返回 401。"""

    response = await client.get("/api/reviews/recent")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid admin token"


@pytest.mark.asyncio
async def test_create_review_rejects_unsafe_web_url(client: AsyncClient) -> None:
    """Unsafe URL schemes must not be stored or rendered by the dashboard."""

    response = await client.post(
        "/api/reviews",
        headers={"X-Internal-Token": "test-internal-token"},
        json={
            "project_id": 123,
            "mr_iid": 7,
            "target_branch": "master",
            "source_branch": "feature/demo",
            "commit_sha": "abc123",
            "web_url": "javascript:alert(1)",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_recent_reviews_returns_latest_manual_review(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dashboard can read a sanitized list of recently triggered reviews.

    验证 DB 失败时回退到内存 deque 的行为仍然正常。
    """

    # 生成合法 admin JWT（/api/reviews/recent 现在走 JWT 认证）
    from datetime import UTC, datetime, timedelta

    from api.admin import _sign_token

    token = _sign_token("admin", datetime.now(UTC) + timedelta(hours=1))
    auth_headers = {"Authorization": f"Bearer {token}"}

    reviews.clear_recent_reviews_for_tests()

    async def fake_review(event: GitLabMergeRequestEvent) -> OrchestratorResult:
        return OrchestratorResult(
            review_id=UUID("00000000-0000-0000-0000-000000000777"),
            project_uuid=event.project_uuid,
            status="done",
            finding_count=2,
            has_blocker=True,
            blocker_count=1,
            policy_applied="master -> BLOCKER",
            note_id=88,
        )

    monkeypatch.setattr(reviews, "review_merge_request_event", fake_review)
    create_response = await client.post(
        "/api/reviews",
        headers={"X-Internal-Token": "test-internal-token"},
        json={
            "project_id": 123,
            "project_path": "group/demo",
            "mr_iid": 7,
            "target_branch": "master",
            "source_branch": "feature/demo",
            "commit_sha": "abc123",
            "title": "Demo MR",
            "web_url": "https://gitlab.example.com/group/demo/-/merge_requests/7",
        },
    )
    assert create_response.status_code == 200

    # GET /recent 走 JWT 认证；DB 查询失败（client fixture 无 DB）时回退到 deque
    list_response = await client.get(
        "/api/reviews/recent",
        headers=auth_headers,
    )

    assert list_response.status_code == 200
    assert list_response.json() == [
        {
            "review_id": "00000000-0000-0000-0000-000000000777",
            "project_id": 123,
            "project_path": "group/demo",
            "mr_iid": 7,
            "title": "Demo MR",
            "web_url": "https://gitlab.example.com/group/demo/-/merge_requests/7",
            "status": "done",
            "has_blocker": True,
            "finding_count": 2,
            "blocker_count": 1,
            "policy_applied": "master -> BLOCKER",
            "review_url": "https://gitlab.example.com/group/demo/-/merge_requests/7#note_88",
            "engine_used": None,
            "created_at": None,
            "review_mode": "full",
            "lifecycle_event": None,
        }
    ]
    reviews.clear_recent_reviews_for_tests()
