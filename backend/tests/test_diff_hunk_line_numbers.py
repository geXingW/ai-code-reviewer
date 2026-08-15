"""多 hunk 场景下的行号计算测试。

一个 ``DiffHunk`` 的 ``content`` 可能包含同一文件的所有真实 hunk
（见 ``review_orchestrator._build_diff_hunks``），行号计算必须为每个
``@@`` header 重置计数器，否则第二个及以后的 hunk 行号全部偏移。
"""

from __future__ import annotations

from engines.llm_engine.engine import (
    LLMDirectEngine,
    _iter_added_lines,
    _line_in_diff,
    _resolve_line_number,
)
from engines.types import DiffHunk


def _multi_hunk() -> DiffHunk:
    """两个 hunk 的 diff：第一个从新文件第 10 行开始，第二个从第 51 行开始。

    新文件行号：
    - hunk 1 (new_start=10): context(10), +added1(11), +added2(12), context(13)
    - hunk 2 (new_start=51): context(51), +added3(52), context(53)
    """

    return DiffHunk(
        file_path="app/main.py",
        old_path="app/main.py",
        new_start=10,
        new_lines=8,
        old_start=10,
        old_lines=7,
        content=(
            "@@ -10,4 +10,4 @@\n"
            " context-a\n"
            "+added1\n"
            "+added2\n"
            " context-b\n"
            "@@ -50,3 +51,3 @@\n"
            " context-c\n"
            "+added3\n"
            " context-d\n"
        ),
    )


def _single_hunk() -> DiffHunk:
    return DiffHunk(
        file_path="app/main.py",
        old_path="app/main.py",
        new_start=10,
        new_lines=3,
        old_start=10,
        old_lines=2,
        content="@@ -10,2 +10,3 @@\n context-a\n+added1\n context-b\n",
    )


class TestIterAddedLines:
    def test_iter_added_lines_single_hunk(self) -> None:
        """单 hunk 回归：行号从 new_start 起按 context/added 递增。"""

        assert _iter_added_lines(_single_hunk()) == [(11, "added1")]

    def test_iter_added_lines_multi_hunk(self) -> None:
        """多 hunk：第二个 hunk 的 added line 必须用其自身 new_start 计数。"""

        assert _iter_added_lines(_multi_hunk()) == [
            (11, "added1"),
            (12, "added2"),
            (52, "added3"),
        ]


class TestMultiHunkLineResolution:
    def test_line_in_diff_multi_hunk(self) -> None:
        hunk = _multi_hunk()
        hunks = [hunk]

        # 第一个 hunk 内的 added line
        assert _line_in_diff("app/main.py", 11, hunks) is True
        # 第二个 hunk 内的 added line（修复前会被算成 16 而判无效）
        assert _line_in_diff("app/main.py", 52, hunks) is True
        # 两个 hunk 之间的未改动区域
        assert _line_in_diff("app/main.py", 30, hunks) is False
        # context 行不算 added line
        assert _line_in_diff("app/main.py", 10, hunks) is False
        assert _line_in_diff("app/main.py", 51, hunks) is False
        # 其他文件
        assert _line_in_diff("app/other.py", 11, hunks) is False

    def test_resolve_line_number_multi_hunk(self) -> None:
        hunk = _multi_hunk()
        hunks = [hunk]

        # 第一个 hunk 内的代码
        assert _resolve_line_number("app/main.py", "added1", hunks) == 11
        # 第二个 hunk 内的代码必须解析到第二个 hunk 的真实行号
        assert _resolve_line_number("app/main.py", "added3", hunks) == 52
        # 不存在的代码
        assert _resolve_line_number("app/main.py", "no-such-code", hunks) is None

    def test_engine_accepts_line_number_in_second_hunk(self) -> None:
        """LLM 返回第二个 hunk 内的行号时，_normalise_raw_finding 不应丢弃。"""

        engine = LLMDirectEngine()
        raw = {
            "file_path": "app/main.py",
            "line_number": 52,
            "rule_id": "no-print-prod",
            "severity": "WARNING",
            "title": "Avoid print in production",
        }
        normalized = engine._normalise_raw_finding(raw, [_multi_hunk()])
        assert normalized is not None
        assert normalized["line_number"] == 52


class TestFormatDiffMultiHunk:
    def test_format_diff_multi_hunk(self) -> None:
        """每个真实 hunk 都有自己的 new_start 标注。"""

        output = LLMDirectEngine._format_diff([_multi_hunk()])

        assert "### File: app/main.py" in output
        assert "#### Hunk: new_start=10, new_lines=4" in output
        assert "#### Hunk: new_start=51, new_lines=3" in output
        # 两个 @@ header 都保留在 diff 代码块里
        assert output.count("@@ -10,4 +10,4 @@") == 1
        assert output.count("@@ -50,3 +51,3 @@") == 1

    def test_format_diff_single_hunk_unchanged_info(self) -> None:
        """单 hunk：文件标题与 new_start 信息保持准确。"""

        output = LLMDirectEngine._format_diff([_single_hunk()])

        assert "### File: app/main.py" in output
        assert "#### Hunk: new_start=10, new_lines=3" in output
        assert "@@ -10,2 +10,3 @@" in output

    def test_format_diff_without_header_falls_back(self) -> None:
        """content 中没有 @@ header 时保持旧的兜底格式。"""

        hunk = DiffHunk(
            file_path="app/main.py",
            new_start=1,
            new_lines=2,
            old_start=1,
            old_lines=2,
            content="+raw content without header",
        )
        output = LLMDirectEngine._format_diff([hunk])

        assert "### File: app/main.py" in output
        assert "new_start=1, new_lines=2" in output
        assert "+raw content without header" in output
