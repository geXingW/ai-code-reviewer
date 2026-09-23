"""Tests for :class:`services.repo_reader.GitLabRepoReader`.

RepoReader 契约的核心：GitLab API 任何失败都必须归一化为 ``[error] ...``
字符串观察，绝不能向上抛异常（agent 循环会把返回值直接回填给模型）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from integrations.gitlab.client import GitLabClient, GitLabClientError
from services.repo_reader import GitLabRepoReader


def _reader(client: AsyncMock) -> GitLabRepoReader:
    return GitLabRepoReader(client, project_id=123, default_ref="head-sha")


def _failing_client(error: Exception) -> AsyncMock:
    client = AsyncMock(spec=GitLabClient)
    client.get_file_contents.side_effect = error
    client.list_repository_tree.side_effect = error
    client.get_file_blame.side_effect = error
    client.search_code.side_effect = error
    client.list_commits.side_effect = error
    return client


@pytest.mark.asyncio
async def test_read_file_uses_default_ref_and_truncates() -> None:
    client = AsyncMock(spec=GitLabClient)
    client.get_file_contents.return_value = "x" * 100

    reader = _reader(client)
    output = await reader.read_file("app.py", "", max_chars=50)

    client.get_file_contents.assert_awaited_once_with(
        project_id=123,
        file_path="app.py",
        ref="head-sha",
    )
    assert output.startswith("x")
    assert "truncated" in output
    assert len(output.splitlines()[0]) == 50


@pytest.mark.asyncio
async def test_read_file_error_becomes_error_observation() -> None:
    client = _failing_client(
        GitLabClientError(status_code=404, message="404 File Not Found", response_body="")
    )

    output = await _reader(client).read_file("missing.py", "main")

    assert output.startswith("[error]")
    assert "404" in output


@pytest.mark.asyncio
async def test_list_tree_formats_type_and_path() -> None:
    client = AsyncMock(spec=GitLabClient)
    client.list_repository_tree.return_value = [
        {"type": "tree", "path": "src"},
        {"type": "blob", "path": "src/app.py"},
    ]

    output = await _reader(client).list_tree("src", "main", recursive=True)

    client.list_repository_tree.assert_awaited_once_with(
        project_id=123,
        path="src",
        ref="main",
        recursive=True,
    )
    assert output == "tree  src\nblob  src/app.py"


@pytest.mark.asyncio
async def test_blame_accumulates_line_numbers_across_ranges() -> None:
    client = AsyncMock(spec=GitLabClient)
    client.get_file_blame.return_value = [
        {"commit": {"id": "aaaa1111ffff"}, "lines": ["one", "two"]},
        {"commit": {"id": "bbbb2222"}, "lines": ["three"]},
    ]

    output = await _reader(client).blame("app.py", "")

    assert "aaaa1111  L1  one" in output
    assert "aaaa1111  L2  two" in output
    # 第二段的行号在第一段之后累加。
    assert "bbbb2222  L3  three" in output


@pytest.mark.asyncio
async def test_search_empty_hits_and_error_paths() -> None:
    client = AsyncMock(spec=GitLabClient)
    client.search_code.return_value = []

    output = await _reader(client).search("no-such-symbol")
    assert output == "[empty] no search hits"

    failing = _failing_client(
        GitLabClientError(status_code=500, message="search timeout", response_body="")
    )
    output = await _reader(failing).search("login")
    assert output.startswith("[error]")


@pytest.mark.asyncio
async def test_commit_history_formats_and_truncates() -> None:
    client = AsyncMock(spec=GitLabClient)
    client.list_commits.return_value = [
        {"id": "abc123def456", "title": "fix: bug", "committed_date": "2026-09-01T10:00:00Z"}
    ]

    output = await _reader(client).commit_history("app.py", "", limit=5)

    client.list_commits.assert_awaited_once_with(
        project_id=123,
        ref="head-sha",
        path="app.py",
        limit=5,
    )
    assert "abc123de" in output
    assert "fix: bug" in output
    assert "2026-09-01" in output


@pytest.mark.asyncio
async def test_unexpected_exception_is_swallowed_as_error_text() -> None:
    client = _failing_client(RuntimeError("boom"))

    output = await _reader(client).read_file("app.py", "main")

    assert output.startswith("[error]")
    assert "boom" in output
