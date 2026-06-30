"""Tests for Jenkins synchronous review trigger API."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient

from app.api import reviews
from app.services.review_orchestrator import GitLabMergeRequestEvent, OrchestratorResult


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
