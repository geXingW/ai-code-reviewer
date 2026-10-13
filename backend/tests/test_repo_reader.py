"""Tests for :class:`services.repo_reader.GitLabRepoReader`.

RepoReader 契约的核心：GitLab API 任何失败都必须归一化为 ``[error] ...``
字符串观察，绝不能向上抛异常（agent 循环会把返回值直接回填给模型）。
"""

from __future__ import annotations

import re
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
    assert output.startswith("[empty] no search hits")
    assert "regex" in output.lower()  # 空结果附带教育提示

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


# ---- read_file 行号区间模式（line_start/line_end） ------------------------------


@pytest.mark.asyncio
async def test_read_file_line_range_prefixes_line_numbers() -> None:
    """行号区间模式：每行前缀真实行号，与 diff 行号同坐标系。"""

    client = AsyncMock(spec=GitLabClient)
    client.get_file_contents.return_value = "alpha\nbeta\ngamma\ndelta\n"

    output = await _reader(client).read_file("app.py", "", line_start=2, line_end=3)

    client.get_file_contents.assert_awaited_once_with(
        project_id=123,
        file_path="app.py",
        ref="head-sha",
    )
    assert output == "2: beta\n3: gamma"


@pytest.mark.asyncio
async def test_read_file_line_range_open_ended_reads_to_eof() -> None:
    """只给 line_start 时读到文件末尾，完整返回不带截断标记。"""

    client = AsyncMock(spec=GitLabClient)
    client.get_file_contents.return_value = "a\nb\nc"

    output = await _reader(client).read_file("app.py", "", line_start=2)

    assert output == "2: b\n3: c"
    assert "truncated" not in output


@pytest.mark.asyncio
async def test_read_file_line_range_truncates_by_chars_with_next_line_start() -> None:
    """区间超过 max_chars 按行截断，并给出下一页 line_start。"""

    client = AsyncMock(spec=GitLabClient)
    client.get_file_contents.return_value = "\n".join(
        f"line-{n}" for n in range(1, 41)
    )

    output = await _reader(client).read_file("app.py", "", line_start=1, max_chars=60)

    assert output.startswith("1: line-1")
    assert "truncated" in output
    match = re.search(r"use line_start=(\d+)", output)
    assert match is not None
    next_start = int(match.group(1))
    # 下一页提示能直接续读且拿到完整尾部。
    rest = await _reader(client).read_file(
        "app.py", "", line_start=next_start, max_chars=5000
    )
    assert f"{next_start}: line-{next_start}" in rest
    assert "40: line-40" in rest
    assert "truncated" not in rest


@pytest.mark.asyncio
async def test_read_file_line_range_error_paths() -> None:
    """非法区间参数归一化为 [error] 观察而不是抛异常。"""

    client = AsyncMock(spec=GitLabClient)
    client.get_file_contents.return_value = "a\nb\n"
    reader = _reader(client)

    beyond = await reader.read_file("app.py", "", line_start=10)
    assert beyond == (
        "[error] read_file line_start 10 is beyond end of file (file has 2 lines)"
    )

    inverted = await reader.read_file("app.py", "", line_start=3, line_end=1)
    assert inverted == "[error] read_file line_end 1 must be >= line_start 3"

    below_one = await reader.read_file("app.py", "", line_start=0)
    assert below_one == "[error] read_file line_start must be >= 1, got 0"


@pytest.mark.asyncio
async def test_read_file_line_range_alone_fits_max_chars_single_line() -> None:
    """首行自己就超过 max_chars 时至少返回一行，不产生空输出。"""

    client = AsyncMock(spec=GitLabClient)
    client.get_file_contents.return_value = "x" * 200 + "\n" + "y" * 10

    output = await _reader(client).read_file("app.py", "", line_start=1, max_chars=50)

    assert output.startswith("1: ")
    assert "use line_start=2" in output
