"""Read-only repository access backed by the GitLab API.

``GitLabRepoReader`` 实现 :class:`engines.types.RepoReader` 结构契约，
供 ``llm-agent`` 引擎在审查时调查提交影响范围（读文件 / 看目录树 /
blame / 代码搜索 / 文件级 commit 历史）。

契约约定（见 RepoReader docstring）：

* 所有方法**失败返回错误描述字符串而不是抛异常**——agent 循环会把
  返回值原样作为观察回填给模型，异常会打断整轮循环。
* 每个方法可传入 ``max_chars`` 截断输出，防止超大文件 / 目录把上下文
  预算一次性吃光。
"""

from __future__ import annotations

import logging
from typing import Any

from integrations.gitlab.client import GitLabClient

logger = logging.getLogger(__name__)

# blame / search 等列表型响应的单条目保留的最大字符数。
_ITEM_MAX_CHARS = 500
# search_code 返回条目里 data（命中片段）字段保留的最大字符数。
_SNIPPET_MAX_CHARS = 300


def _truncate(text: str, max_chars: int) -> str:
    """按字符数截断并附截断标记。"""

    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... (truncated, total {len(text)} chars)"


def _error_text(action: str, exc: Exception) -> str:
    """把异常归一化成模型可读的错误观察。"""

    return f"[error] {action} failed: {exc}"


class GitLabRepoReader:
    """RepoReader 实现包装 GitLabClient + 固定 project/ref。"""

    def __init__(
        self,
        client: GitLabClient,
        *,
        project_id: int,
        default_ref: str,
    ) -> None:
        """Create a reader.

        Args:
            client: 已配置好 base_url/token 的 GitLabClient。
            project_id: 数值型 GitLab 项目 ID。
            default_ref: 工具未显式传 ref 时使用的默认 ref（MR head SHA）。
        """

        self._client = client
        self._project_id = project_id
        self._default_ref = default_ref

    async def read_file(
        self,
        file_path: str,
        ref: str = "",
        *,
        max_chars: int = 8000,
    ) -> str:
        """读取文件全文；ref 为空时回退 default_ref。"""

        effective_ref = ref or self._default_ref
        try:
            content = await self._client.get_file_contents(
                project_id=self._project_id,
                file_path=file_path,
                ref=effective_ref,
            )
        except Exception as exc:  # noqa: BLE001 - 契约：任何失败都归一化为错误观察
            logger.info("repo_reader read_file failed", extra={"file_path": file_path})
            return _error_text(f"read_file({file_path}@{effective_ref})", exc)
        return _truncate(content, max_chars)

    async def list_tree(
        self,
        path: str,
        ref: str = "",
        *,
        recursive: bool = False,
        max_chars: int = 4000,
    ) -> str:
        """返回目录树条目列表（每行 ``type  path``）。"""

        effective_ref = ref or self._default_ref
        try:
            entries = await self._client.list_repository_tree(
                project_id=self._project_id,
                path=path,
                ref=effective_ref,
                recursive=recursive,
            )
        except Exception as exc:  # noqa: BLE001 - 契约：任何失败都归一化为错误观察
            logger.info("repo_reader list_tree failed", extra={"path": path})
            return _error_text(f"list_tree({path}@{effective_ref})", exc)
        lines = [
            f"{entry.get('type', 'unknown')}  {entry.get('path', '')}"
            for entry in entries
        ]
        return _truncate("\n".join(lines), max_chars)

    async def blame(self, file_path: str, ref: str = "", *, max_chars: int = 6000) -> str:
        """返回紧凑 blame 视图（每行 ``sha  lineno  content``）。"""

        effective_ref = ref or self._default_ref
        try:
            ranges = await self._client.get_file_blame(
                project_id=self._project_id,
                file_path=file_path,
                ref=effective_ref,
            )
        except Exception as exc:  # noqa: BLE001 - 契约：任何失败都归一化为错误观察
            logger.info("repo_reader blame failed", extra={"file_path": file_path})
            return _error_text(f"blame({file_path}@{effective_ref})", exc)
        lines: list[str] = []
        next_line = 1
        for rng in ranges:
            commit = rng.get("commit")
            sha = ""
            if isinstance(commit, dict):
                sha = str(commit.get("id", ""))[:8]
            range_lines, next_line = _iter_blame_lines(rng, next_line)
            for line_no, content in range_lines:
                lines.append(f"{sha}  L{line_no}  {content[:_ITEM_MAX_CHARS]}")
        return _truncate("\n".join(lines), max_chars)

    async def search(self, query: str, *, max_chars: int = 4000) -> str:
        """返回 blob 搜索命中（每行 ``path:startline  snippet``）。"""

        try:
            hits = await self._client.search_code(project_id=self._project_id, query=query)
        except Exception as exc:  # noqa: BLE001 - 契约：任何失败都归一化为错误观察
            # search 依赖实例索引，不可用时明确告知模型换路径调查。
            logger.info("repo_reader search failed", extra={"query": query[:100]})
            return _error_text(f"search({query!r})", exc)
        lines = []
        for hit in hits:
            path = str(hit.get("path", ""))
            startline = hit.get("startline", "?")
            snippet = str(hit.get("data", ""))[:_SNIPPET_MAX_CHARS].replace("\n", " ")
            lines.append(f"{path}:{startline}  {snippet}")
        if not lines:
            return "[empty] no search hits"
        return _truncate("\n".join(lines), max_chars)

    async def commit_history(
        self,
        file_path: str,
        ref: str = "",
        limit: int = 10,
        *,
        max_chars: int = 3000,
    ) -> str:
        """返回影响指定文件的最近 commit（每行 ``sha  date  title``）。"""

        effective_ref = ref or self._default_ref
        try:
            commits = await self._client.list_commits(
                project_id=self._project_id,
                ref=effective_ref,
                path=file_path,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001 - 契约：任何失败都归一化为错误观察
            logger.info("repo_reader commit_history failed", extra={"file_path": file_path})
            return _error_text(f"commit_history({file_path}@{effective_ref})", exc)
        lines = [
            f"{str(c.get('id', ''))[:8]}  {c.get('committed_date', '')[:10]}  "
            f"{str(c.get('title', ''))[:_ITEM_MAX_CHARS]}"
            for c in commits
        ]
        return _truncate("\n".join(lines), max_chars)


def _iter_blame_lines(blame_range: dict[str, Any], start: int) -> tuple[list[tuple[int, str]], int]:
    """从单个 blame 段提取 ``(行号, 内容)`` 列表，返回下一个可用行号。

    GitLab blame 段不带显式起始行号，按段顺序全局累加得到行号。
    """

    lines = blame_range.get("lines")
    if not isinstance(lines, list):
        return [], start
    result: list[tuple[int, str]] = []
    line_no = start
    for line in lines:
        if isinstance(line, str):
            result.append((line_no, line))
            line_no += 1
    return result, line_no


__all__ = ["GitLabRepoReader"]
