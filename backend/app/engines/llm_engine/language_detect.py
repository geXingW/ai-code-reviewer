"""语言探测：从 diff 涉及的文件后缀推断需要叠加的语言 checklist。

单独成模块方便测试和后续扩展；未识别的后缀直接忽略，返回值保序去重。
"""

from __future__ import annotations

import os

from app.engines.types import DiffHunk

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
}


def detect_languages(diff_hunks: list[DiffHunk]) -> list[str]:
    """从 diff 涉及的文件路径推断语言 slug 列表，去重且保持首次出现顺序。

    未识别的后缀（.md / .yaml / .json 等）直接忽略。返回全部小写 slug。
    """

    ordered: list[str] = []
    seen: set[str] = set()
    for hunk in diff_hunks:
        _, ext = os.path.splitext(hunk.file_path)
        language = _EXT_TO_LANG.get(ext.lower())
        if language is None or language in seen:
            continue
        seen.add(language)
        ordered.append(language)
    return ordered
