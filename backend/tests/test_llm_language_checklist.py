"""语言 checklist 探测与渲染的单元测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.llm_engine.engine import LLMDirectEngine
from app.engines.llm_engine.language_detect import detect_languages
from app.engines.types import DiffHunk, ProviderConfig, ReviewContext


def _hunk(file_path: str) -> DiffHunk:
    """构造一个最小可用的 DiffHunk；内容只影响 diff 渲染，不影响语言探测。"""

    return DiffHunk(
        file_path=file_path,
        old_path=file_path,
        new_start=1,
        new_lines=1,
        old_start=1,
        old_lines=0,
        content="@@ -1,0 +1,1 @@\n+print('hi')\n",
    )


def _ctx(diff_hunks: list[DiffHunk]) -> ReviewContext:
    return ReviewContext(
        review_id=uuid4(),
        project_id=uuid4(),
        mr_iid="1",
        source_branch="feature/x",
        target_branch="master",
        source_commit_sha="aaaa",
        target_commit_sha="bbbb",
        diff_hunks=diff_hunks,
        rules=[],
        provider=ProviderConfig(
            provider_id=uuid4(),
            provider_type="openai-compatible",
            base_url="https://example.com/v1",
            model="test",
            api_key="k",
        ),
        history=[],
        mr_title="stub",
        mr_description="",
        last_commit_message="",
    )


def test_detect_languages_python_only() -> None:
    """diff 仅含 .py 时返回单元素 ["python"]。"""

    result = detect_languages([_hunk("app/main.py")])
    assert result == ["python"]


def test_detect_languages_mixed() -> None:
    """.py + .ts + .md 混合时忽略 .md，保留 python 和 typescript。"""

    result = detect_languages(
        [
            _hunk("app/main.py"),
            _hunk("web/src/index.ts"),
            _hunk("README.md"),
        ]
    )
    assert result == ["python", "typescript"]


def test_detect_languages_dedup_preserves_order() -> None:
    """两个 .py 文件应只返回一次 python。"""

    result = detect_languages([_hunk("a.py"), _hunk("b.py")])
    assert result == ["python"]


def test_detect_languages_dedup_preserves_first_seen_order() -> None:
    """多语言 + 重复：应按首次出现顺序保留一次。"""

    result = detect_languages(
        [
            _hunk("web/src/a.ts"),
            _hunk("app/x.py"),
            _hunk("web/src/b.tsx"),
            _hunk("app/y.py"),
        ]
    )
    assert result == ["typescript", "python"]


def test_format_language_checklists_empty() -> None:
    """空 languages 返回默认占位文案。"""

    rendered = LLMDirectEngine._format_language_checklists([])
    assert rendered == "No specific language checklists apply to this diff."


def test_format_language_checklists_python() -> None:
    """python checklist 段落包含标题和 md 正文中的关键词。"""

    rendered = LLMDirectEngine._format_language_checklists(["python"])
    assert "### Python checklist" in rendered
    # python.md 里应存在的具体指令关键词
    assert "mutable default argument" in rendered


def test_format_language_checklists_unknown_language_skipped() -> None:
    """未知语言（无 md 文件）应被静默跳过，不抛异常也不占位。"""

    rendered = LLMDirectEngine._format_language_checklists(["python", "rust"])
    assert "### Python checklist" in rendered
    # 未知语言不应生成对应段落标题
    assert "### Rust checklist" not in rendered


@pytest.mark.asyncio
async def test_build_prompt_injects_language_checklist_section() -> None:
    """_build_prompt 应把 language checklist section 注入 user prompt。"""

    engine = LLMDirectEngine()
    ctx = _ctx([_hunk("app/service.py")])
    prompt = engine._build_prompt(ctx)

    assert "## Language-specific checklist" in prompt
    assert "### Python checklist" in prompt
    # 占位符不应残留
    assert "{{language_checklist_block}}" not in prompt


@pytest.mark.asyncio
async def test_build_prompt_uses_placeholder_when_no_recognized_language() -> None:
    """diff 只含非代码文件时，section 落到默认占位文案。"""

    engine = LLMDirectEngine()
    ctx = _ctx([_hunk("docs/spec.md"), _hunk("data.json")])
    prompt = engine._build_prompt(ctx)

    assert "## Language-specific checklist" in prompt
    assert "No specific language checklists apply to this diff." in prompt
