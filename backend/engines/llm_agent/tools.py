"""Agent tools backed by the read-only :class:`~engines.types.RepoReader`.

每个工具 = 名字 + 描述 + JSON Schema 参数定义 + ``execute()``。工具结果一律
返回字符串，失败返回 ``[error] ...`` 观察而不是抛异常，方便 agent 循环把
错误原样回填给模型。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from engines.types import RepoReader


class AgentTool(Protocol):
    """Structural contract one agent tool must satisfy."""

    @property
    def name(self) -> str:
        """Unique tool name shown to the model."""

        ...

    @property
    def description(self) -> str:
        """Short usage hint shown to the model."""

        ...

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for the tool arguments."""

        ...

    async def execute(self, args: dict[str, Any]) -> str:
        """Run the tool and return the observation text."""

        ...


@dataclass(frozen=True)
class _RepoTool:
    """一个绑定 RepoReader 实例的只读工具。"""

    reader: RepoReader
    name: str
    description: str
    parameters: dict[str, Any]

    async def execute(self, args: dict[str, Any]) -> str:
        handler = _HANDLERS[self.name]
        return await handler(self.reader, args)


_REF_DESCRIPTION = (
    "Git ref (branch / tag / commit SHA). Defaults to the MR head commit "
    "when omitted."
)


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def _as_bool(value: object) -> bool:
    return bool(value) and value != "false"


def _as_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except ValueError:
        return default
    return max(minimum, min(parsed, maximum))


def _optional_int(value: object, *, minimum: int, maximum: int) -> int | None:
    """解析可选整型参数；缺省/空串/非法值返回 None。"""

    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        parsed = int(str(value))
    except ValueError:
        return None
    return max(minimum, min(parsed, maximum))


async def _read_file(reader: RepoReader, args: dict[str, Any]) -> str:
    file_path = str(args.get("file_path") or "")
    if not file_path.strip():
        return "[error] read_file requires non-empty file_path"
    ref = str(args.get("ref") or "")
    max_chars = _as_int(args.get("max_chars"), 8000, minimum=200, maximum=50000)
    offset = _as_int(args.get("offset"), 0, minimum=0, maximum=10_000_000)
    line_start = _optional_int(args.get("line_start"), minimum=1, maximum=10_000_000)
    line_end = _optional_int(args.get("line_end"), minimum=1, maximum=10_000_000)
    # line 参数仅在显式给出时透传：兼容未声明这两个 keyword 的 RepoReader
    # 实现（本地 clone reader / 测试替身），避免 TypeError 被归一化为错误观察。
    kwargs: dict[str, Any] = {"max_chars": max_chars, "offset": offset}
    if line_start is not None or line_end is not None:
        kwargs["line_start"] = line_start
        kwargs["line_end"] = line_end
    return await reader.read_file(file_path, ref, **kwargs)


async def _list_tree(reader: RepoReader, args: dict[str, Any]) -> str:
    path = str(args.get("path") or "")
    ref = str(args.get("ref") or "")
    recursive = _as_bool(args.get("recursive", False))
    return await reader.list_tree(path, ref, recursive=recursive)


async def _get_blame(reader: RepoReader, args: dict[str, Any]) -> str:
    file_path = str(args.get("file_path") or "")
    if not file_path.strip():
        return "[error] get_blame requires non-empty file_path"
    ref = str(args.get("ref") or "")
    return await reader.blame(file_path, ref)


async def _search_code(reader: RepoReader, args: dict[str, Any]) -> str:
    query = str(args.get("query") or "")
    if not query.strip():
        return "[error] search_code requires non-empty query"
    return await reader.search(query)


async def _file_history(reader: RepoReader, args: dict[str, Any]) -> str:
    file_path = str(args.get("file_path") or "")
    if not file_path.strip():
        return "[error] get_file_history requires non-empty file_path"
    ref = str(args.get("ref") or "")
    limit = _as_int(args.get("limit"), 10, minimum=1, maximum=50)
    return await reader.commit_history(file_path, ref, limit=limit)


_HANDLERS = {
    "read_file": _read_file,
    "list_tree": _list_tree,
    "get_blame": _get_blame,
    "search_code": _search_code,
    "get_file_history": _file_history,
}


def build_default_tools(reader: RepoReader) -> list[AgentTool]:
    """Build the built-in read-only toolset bound to ``reader``."""

    # 五个工具刻意都是只读的：审查过程不允许改仓库，否则"AI 自己改代码再自评"
    # 会污染结论。新增工具时同样要保持只读，并且把"什么时候该用它"写进
    # description —— 这些 description 是**给模型看的文档**，不是给人看的注释，
    # 模型能读到的只有 name + description + 参数 schema。
    #
    # 覆盖的调查路径：定位文件（list_tree）→ 读目标区域（read_file，优先行号、
    # 大文件用 offset 翻页）→ 追调用方/影响面（search_code，只支持单个关键词）
    # → 判断历史与责任（get_blame / get_file_history）。
    # 每个工具的返回都会被引擎按 agent_tool_result_max_chars 截断并计入预算，
    # 所以 description 里要明确"分页/只查单个标识符"，否则模型容易一次拉满。
    return [
        _RepoTool(
            reader=reader,
            name="read_file",
            description=(
                "Read the content of one file from the repository. Two paging "
                "modes: (1) line-based — pass line_start/line_end (1-based, the "
                "same NEW-file line numbers you see in the diff) and every "
                "returned line is prefixed with its number; use this to inspect "
                "the region around a diff hunk and to quote exact line numbers "
                "in findings. (2) char-based — pass offset to page through a "
                "huge file when you do not know line numbers. Use it to inspect "
                "callers/callees of changed functions and to verify assumptions "
                "before reporting a finding."
            ),
            parameters=_schema(
                {
                    "file_path": {
                        "type": "string",
                        "description": "Repository-relative file path, e.g. src/app.py.",
                    },
                    "ref": {"type": "string", "description": _REF_DESCRIPTION},
                    "line_start": {
                        "type": "integer",
                        "description": (
                            "1-based line number to start reading from (new-file "
                            "line numbers, matching diff line numbers). Every "
                            "returned line is prefixed with its number. Preferred "
                            "way to read a specific region."
                        ),
                    },
                    "line_end": {
                        "type": "integer",
                        "description": (
                            "1-based inclusive end line (defaults to end of file)."
                        ),
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Max characters per page (default 8000).",
                    },
                    "offset": {
                        "type": "integer",
                        "description": (
                            "Char offset to start reading from (default 0). Only "
                            "for char-wise paging of huge files; ignored when "
                            "line_start is given. Use the next-page offset from a "
                            "truncated result to page through large files."
                        ),
                    },
                },
                required=["file_path"],
            ),
        ),
        _RepoTool(
            reader=reader,
            name="list_tree",
            description=(
                "List files/directories under a path. Use it to understand the "
                "project layout around changed files."
            ),
            parameters=_schema(
                {
                    "path": {
                        "type": "string",
                        "description": "Directory path; empty means repo root.",
                    },
                    "ref": {"type": "string", "description": _REF_DESCRIPTION},
                    "recursive": {
                        "type": "boolean",
                        "description": "Recurse into subdirectories (default false).",
                    },
                },
                required=[],
            ),
        ),
        _RepoTool(
            reader=reader,
            name="get_blame",
            description=(
                "Per-line blame of a file (last commit per line). Use it to "
                "check when/why surrounding code was introduced."
            ),
            parameters=_schema(
                {
                    "file_path": {"type": "string", "description": "Repository-relative path."},
                    "ref": {"type": "string", "description": _REF_DESCRIPTION},
                },
                required=["file_path"],
            ),
        ),
        _RepoTool(
            reader=reader,
            name="search_code",
            description=(
                "Full-text code search across the project (GitLab blob scope). "
                "Use it to find usages of a changed symbol and assess the impact "
                "scope. IMPORTANT: this is keyword full-text search — it does "
                "NOT support regex, wildcards, or multi-word phrases. Query a "
                "single short identifier (e.g. a class or method name)."
            ),
            parameters=_schema(
                {
                    "query": {
                        "type": "string",
                        "description": (
                            "A single short keyword, e.g. one class or method "
                            "name. No regex, no wildcards."
                        ),
                    },
                },
                required=["query"],
            ),
        ),
        _RepoTool(
            reader=reader,
            name="get_file_history",
            description=(
                "Recent commits touching one file. Use it to understand the "
                "evolution and hot-spot status of the file."
            ),
            parameters=_schema(
                {
                    "file_path": {"type": "string", "description": "Repository-relative path."},
                    "ref": {"type": "string", "description": _REF_DESCRIPTION},
                    "limit": {
                        "type": "integer",
                        "description": "Max commits returned (default 10).",
                    },
                },
                required=["file_path"],
            ),
        ),
    ]


def tool_to_spec(tool: AgentTool) -> dict[str, Any]:
    """Serialize a tool into the provider-neutral ``ToolSpec`` fields."""

    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }


def parse_tool_arguments(raw: str) -> dict[str, Any] | None:
    """Parse a model-supplied arguments JSON string; None when malformed."""

    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = [
    "AgentTool",
    "build_default_tools",
    "parse_tool_arguments",
    "tool_to_spec",
]
