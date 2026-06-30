"""Tests for the GitLab HTTP client wrapper."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from app.integrations.gitlab.client import GitLabClient, GitLabClientError


@pytest.mark.asyncio
@respx.mock
async def test_get_merge_request_changes_normalizes_base_url() -> None:
    """Client strips trailing slash and calls GitLab MR changes endpoint."""

    route = respx.get(
        "https://gitlab.example.com/api/v4/projects/123/merge_requests/7/changes"
    ).mock(
        return_value=Response(
            200,
            json={
                "iid": 7,
                "changes": [
                    {
                        "new_path": "app.py",
                        "old_path": "app.py",
                        "diff": "@@ -1 +1 @@\n-print('old')\n+print('new')\n",
                        "new_file": False,
                        "deleted_file": False,
                    }
                ],
            },
        )
    )

    client = GitLabClient(base_url="https://gitlab.example.com/", token="secret")
    changes = await client.get_merge_request_changes(project_id=123, mr_iid=7)

    assert route.called
    request = route.calls.last.request
    assert request.headers["PRIVATE-TOKEN"] == "secret"
    assert changes["iid"] == 7
    assert changes["changes"][0]["new_path"] == "app.py"


@pytest.mark.asyncio
@respx.mock
async def test_create_merge_request_note_posts_body() -> None:
    """Client can write a note back to the target MR."""

    route = respx.post(
        "https://gitlab.example.com/api/v4/projects/123/merge_requests/7/notes"
    ).mock(return_value=Response(201, json={"id": 99, "body": "done"}))

    client = GitLabClient(base_url="https://gitlab.example.com", token="secret")
    note = await client.create_merge_request_note(
        project_id=123,
        mr_iid=7,
        body="AI Review completed",
    )

    assert route.called
    assert note["id"] == 99
    assert route.calls.last.request.content == b'{"body":"AI Review completed"}'


@pytest.mark.asyncio
@respx.mock
async def test_create_merge_request_discussion_posts_position_payload() -> None:
    """Client can create a line-level discussion on a merge request diff."""

    route = respx.post(
        "https://gitlab.example.com/api/v4/projects/123/merge_requests/7/discussions"
    ).mock(return_value=Response(201, json={"id": "discussion-1"}))

    client = GitLabClient(base_url="https://gitlab.example.com", token="secret")
    discussion = await client.create_merge_request_discussion(
        project_id=123,
        mr_iid=7,
        body="**[BLOCKER] Secret leaked**",
        base_sha="base456",
        start_sha="start789",
        head_sha="abc123",
        old_path="app.py",
        new_path="app.py",
        line_number=10,
    )

    assert discussion["id"] == "discussion-1"
    payload = json.loads(route.calls.last.request.content)
    assert payload == {
        "body": "**[BLOCKER] Secret leaked**",
        "position": {
            "position_type": "text",
            "base_sha": "base456",
            "start_sha": "start789",
            "head_sha": "abc123",
            "old_path": "app.py",
            "new_path": "app.py",
            "new_line": 10,
        },
    }


@pytest.mark.asyncio
@respx.mock
async def test_set_commit_status_sends_expected_payload() -> None:
    """Client wraps GitLab commit status API for CI-style feedback."""

    route = respx.post(
        "https://gitlab.example.com/api/v4/projects/123/statuses/abc123"
    ).mock(return_value=Response(201, json={"status": "success"}))

    client = GitLabClient(base_url="https://gitlab.example.com", token="secret")
    status = await client.set_commit_status(
        project_id=123,
        commit_sha="abc123",
        state="success",
        name="ai-code-reviewer",
        description="No blocking findings",
        target_url="https://review.example.com/reviews/1",
    )

    assert status["status"] == "success"
    payload = route.calls.last.request.content.decode()
    assert "ai-code-reviewer" in payload
    assert "No blocking findings" in payload


@pytest.mark.asyncio
@respx.mock
async def test_gitlab_errors_raise_typed_exception() -> None:
    """Non-2xx GitLab responses are wrapped in GitLabClientError."""

    respx.get(
        "https://gitlab.example.com/api/v4/projects/123/merge_requests/7/changes"
    ).mock(return_value=Response(404, json={"message": "Not found"}))

    client = GitLabClient(base_url="https://gitlab.example.com", token="secret")
    with pytest.raises(GitLabClientError) as exc_info:
        await client.get_merge_request_changes(project_id=123, mr_iid=7)

    assert exc_info.value.status_code == 404
    assert "Not found" in str(exc_info.value)
