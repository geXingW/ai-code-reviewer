"""Tests for Jenkins synchronous review trigger API."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from api import reviews
from models.finding import Finding
from models.project import Project
from models.review import Review
from services.review_orchestrator import GitLabMergeRequestEvent, OrchestratorResult


@pytest.mark.asyncio
async def test_create_review_rejects_missing_internal_token(db_client: AsyncClient) -> None:
    """Jenkins review API requires a server-to-server internal token."""

    response = await db_client.post(
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
    db_client: AsyncClient,
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

    response = await db_client.post(
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
    db_client: AsyncClient,
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

    response = await db_client.post(
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
async def test_recent_reviews_rejects_missing_auth(db_client: AsyncClient) -> None:
    """Dashboard recent reviews endpoint 受 admin JWT 保护，缺 token 返回 401。"""

    response = await db_client.get("/api/reviews/recent")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid admin token"


@pytest.mark.asyncio
async def test_create_review_rejects_unsafe_web_url(db_client: AsyncClient) -> None:
    """Unsafe URL schemes must not be stored or rendered by the dashboard."""

    response = await db_client.post(
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
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """Dashboard can read a sanitized list of recently triggered reviews.

    The /api/reviews/recent endpoint now queries the database directly
    (with an in-memory deque fallback on DB failure), so we seed real
    Project + Review rows and verify the mapping is correct.
    """

    from datetime import UTC, datetime, timedelta

    from api.admin import _sign_token

    # 生成合法 admin JWT（/api/reviews/recent 现在走 JWT 认证）
    token = _sign_token("admin", datetime.now(UTC) + timedelta(hours=1))
    auth_headers = {"Authorization": f"Bearer {token}"}

    # 构造 Project + Review + Finding 数据
    async with db_session_factory() as session:
        project = Project(
            name="group/demo",
            gitlab_project_id="123",
            gitlab_base_url="https://gitlab.example.com",
            gitlab_access_token="glpat-test",
            webhook_secret="test-secret",
        )
        session.add(project)
        await session.flush()

        review = Review(
            id=UUID("00000000-0000-0000-0000-000000000777"),
            project_id=project.id,
            mr_iid="7",
            source_branch="feature/demo",
            target_branch="master",
            commit_sha="abc123",
            status="done",
            has_blocker=True,
            finding_count=2,
            review_mode="full",
        )
        session.add(review)

        # 一个 BLOCKER + 一个 WARNING，blocker_count 应该 = 1
        session.add_all([
            Finding(
                review_id=review.id,
                file_path="app/main.py",
                line_number=10,
                severity="BLOCKER",
                rule_id="SEC-001",
                title="SQL injection in user input",
                description="test blocker",
                status="open",
            ),
            Finding(
                review_id=review.id,
                file_path="app/utils.py",
                line_number=20,
                severity="WARNING",
                rule_id="STYLE-001",
                title="Unused import",
                description="test warning",
                status="open",
            ),
        ])
        await session.commit()

    list_response = await db_client.get(
        "/api/reviews/recent",
        headers=auth_headers,
    )

    assert list_response.status_code == 200
    data = list_response.json()
    assert len(data) == 1
    item = data[0]
    assert item["review_id"] == "00000000-0000-0000-0000-000000000777"
    assert item["project_id"] == 123
    assert item["project_path"] == "group/demo"
    assert item["mr_iid"] == 7
    assert item["title"] == "MR !7"
    assert item["status"] == "done"
    assert item["has_blocker"] is True
    assert item["finding_count"] == 2
    assert item["blocker_count"] == 1
    assert item["review_mode"] == "full"
    # DB 路径下这些字段暂无来源，留空
    assert item["web_url"] is None
    assert item["review_url"] is None
    assert item["policy_applied"] is None
    assert item["engine_used"] is None
    assert item["lifecycle_event"] is None
    assert item["created_at"] is not None
    reviews.clear_recent_reviews_for_tests()
