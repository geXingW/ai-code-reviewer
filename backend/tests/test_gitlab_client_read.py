"""Tests for the read-only GitLab repository APIs used by the agent engine."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from integrations.gitlab.client import GitLabClient, GitLabClientError

BASE = "https://gitlab.example.com"


@pytest.mark.asyncio
@respx.mock
async def test_get_file_contents_encodes_path_and_returns_text() -> None:
    """Nested paths are percent-encoded and raw text is returned."""

    route = respx.get(
        f"{BASE}/api/v4/projects/123/repository/files/src%2Fapp%2Epy/raw"
    ).mock(return_value=Response(200, text="print('hello')\n"))

    client = GitLabClient(base_url=BASE, token="secret")
    content = await client.get_file_contents(project_id=123, file_path="src/app.py", ref="abc123")

    assert route.called
    assert "ref=abc123" in str(route.calls.last.request.url)
    assert content == "print('hello')\n"


@pytest.mark.asyncio
@respx.mock
async def test_get_file_contents_404_raises() -> None:
    respx.get(f"{BASE}/api/v4/projects/123/repository/files/nope%2Epy/raw").mock(
        return_value=Response(404, json={"message": "404 File Not Found"})
    )

    client = GitLabClient(base_url=BASE, token="secret")
    with pytest.raises(GitLabClientError):
        await client.get_file_contents(project_id=123, file_path="nope.py", ref="main")


@pytest.mark.asyncio
@respx.mock
async def test_list_repository_tree_returns_entries() -> None:
    route = respx.get(f"{BASE}/api/v4/projects/123/repository/tree").mock(
        return_value=Response(
            200,
            json=[
                {"id": "1", "name": "src", "type": "tree", "path": "src"},
                {"id": "2", "name": "app.py", "type": "blob", "path": "src/app.py"},
            ],
        )
    )

    client = GitLabClient(base_url=BASE, token="secret")
    entries = await client.list_repository_tree(
        project_id=123, path="src", ref="main", recursive=True
    )

    assert route.called
    url = str(route.calls.last.request.url)
    assert "ref=main" in url
    assert "path=src" in url
    assert "recursive=true" in url
    assert [entry["path"] for entry in entries] == ["src", "src/app.py"]


@pytest.mark.asyncio
@respx.mock
async def test_get_file_blame_returns_ranges() -> None:
    route = respx.get(
        f"{BASE}/api/v4/projects/123/repository/files/src%2Fapp%2Epy/blame"
    ).mock(
        return_value=Response(
            200,
            json=[
                {
                    "commit": {"id": "aaaa1111bbbb"},
                    "lines": ["line one", "line two"],
                }
            ],
        )
    )

    client = GitLabClient(base_url=BASE, token="secret")
    ranges = await client.get_file_blame(project_id=123, file_path="src/app.py", ref="main")

    assert route.called
    assert ranges[0]["commit"]["id"] == "aaaa1111bbbb"
    assert ranges[0]["lines"] == ["line one", "line two"]


@pytest.mark.asyncio
@respx.mock
async def test_search_code_uses_blob_scope() -> None:
    route = respx.get(f"{BASE}/api/v4/projects/123/search").mock(
        return_value=Response(
            200,
            json=[
                {
                    "path": "src/app.py",
                    "data": "def login(): ...",
                    "startline": 10,
                }
            ],
        )
    )

    client = GitLabClient(base_url=BASE, token="secret")
    hits = await client.search_code(project_id=123, query="login")

    assert route.called
    url = str(route.calls.last.request.url)
    assert "scope=blobs" in url
    assert "search=login" in url
    assert hits[0]["path"] == "src/app.py"


@pytest.mark.asyncio
@respx.mock
async def test_list_commits_filters_by_path() -> None:
    route = respx.get(f"{BASE}/api/v4/projects/123/repository/commits").mock(
        return_value=Response(
            200,
            json=[{"id": "abc123", "title": "fix: bug", "committed_date": "2026-09-01"}],
        )
    )

    client = GitLabClient(base_url=BASE, token="secret")
    commits = await client.list_commits(project_id=123, ref="main", path="src/app.py", limit=5)

    assert route.called
    url = str(route.calls.last.request.url)
    assert "ref_name=main" in url
    assert "path=src%2Fapp.py" in url or "path=src/app.py" in url
    assert commits[0]["id"] == "abc123"


@pytest.mark.asyncio
async def test_read_methods_reject_empty_args() -> None:
    client = GitLabClient(base_url=BASE, token="secret")

    with pytest.raises(ValueError):
        await client.get_file_contents(project_id=123, file_path=" ", ref="main")
    with pytest.raises(ValueError):
        await client.get_file_contents(project_id=123, file_path="app.py", ref="")
    with pytest.raises(ValueError):
        await client.list_repository_tree(project_id=123, ref="")
    with pytest.raises(ValueError):
        await client.get_file_blame(project_id=123, file_path="app.py", ref="")
    with pytest.raises(ValueError):
        await client.search_code(project_id=123, query=" ")
    with pytest.raises(ValueError):
        await client.list_commits(project_id=123, ref="")
