"""项目级负样本提示词注入测试（engine 侧）。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engines.llm_engine.engine import (
    LLMDirectEngine,
    _load_negative_prompt,
    _reset_negative_prompt_cache,
)
from engines.types import ReviewHistoryItem
from models.negative_example import NegativeExample
from models.project import Project
from models.project_negative_prompt import ProjectNegativePrompt


@pytest.fixture(autouse=True)
def _clean_negative_prompt_cache() -> Iterator[None]:
    """每个测试前后清空 per-project 缓存，避免用例间互相污染。"""

    _reset_negative_prompt_cache()
    yield
    _reset_negative_prompt_cache()


async def _create_project(
    db_session_factory: async_sessionmaker[AsyncSession],
    name: str,
    *,
    prompt_content: str | None = None,
) -> Project:
    """在测试库里建一个项目（可选写入负样本提示词）并返回。"""

    async with db_session_factory() as session:
        project = Project(
            name=name,
            gitlab_project_id=str(uuid4().int % 10_000_000),
            gitlab_access_token="token",
            webhook_secret="secret",
        )
        session.add(project)
        await session.flush()
        if prompt_content is not None:
            session.add(
                ProjectNegativePrompt(project_id=project.id, content=prompt_content)
            )
        await session.commit()
        await session.refresh(project)
        return project


async def _history() -> list[ReviewHistoryItem]:
    """构造一条结构化负样本 history，供回退断言用。"""

    return [
        ReviewHistoryItem(
            rule_id="sql-injection",
            file_path="app/db.py",
            line_number=10,
            title="SQL 拼接",
            description="cursor.execute(f'SELECT {x}')",
            review_note="已白名单校验",
            confirmed_at="2026-08-01T00:00:00Z",
        )
    ]


@pytest.mark.asyncio
async def test_load_negative_prompt_per_project_value(
    db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """项目级有值：读取到该项目提示词；无记录项目读取为空。"""

    project_a = await _create_project(
        db_session_factory, "engine-neg-a", prompt_content="项目A提示词"
    )
    project_b = await _create_project(db_session_factory, "engine-neg-b")

    assert await _load_negative_prompt(project_a.id) == "项目A提示词"
    assert await _load_negative_prompt(project_b.id) == ""


@pytest.mark.asyncio
async def test_resolve_uses_project_prompt_when_set(
    db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """项目级有值 -> history_block 用项目级提示词（不用结构化 history）。"""

    project = await _create_project(
        db_session_factory, "engine-resolve-a", prompt_content="项目级误报模式"
    )
    text = await LLMDirectEngine._resolve_negative_prompt_text(await _history(), project.id)
    assert text == "项目级误报模式"


@pytest.mark.asyncio
async def test_resolve_falls_back_to_structured_history(
    db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """项目级为空 -> 回退 _format_history 结构化输出。"""

    project = await _create_project(db_session_factory, "engine-resolve-b")
    text = await LLMDirectEngine._resolve_negative_prompt_text(await _history(), project.id)
    assert text == LLMDirectEngine._format_history(await _history())
    assert "sql-injection" in text


@pytest.mark.asyncio
async def test_per_project_cache_isolation(
    db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A 项目缓存命中不影响 B 项目查库（缓存互不干扰）。"""

    project_a = await _create_project(
        db_session_factory, "engine-cache-a", prompt_content="A 提示词"
    )
    project_b = await _create_project(
        db_session_factory, "engine-cache-b", prompt_content="B 提示词"
    )

    # A 命中缓存后，B 仍能读到自己的值（说明 B 走了独立缓存槽）。
    assert await _load_negative_prompt(project_a.id) == "A 提示词"
    assert await _load_negative_prompt(project_a.id) == "A 提示词"
    assert await _load_negative_prompt(project_b.id) == "B 提示词"

    # B 更新数据库后 60s 内仍读缓存值（未过期），A 的缓存不受影响。
    async with db_session_factory() as session:
        prompt = await session.get(ProjectNegativePrompt, project_b.id)
        assert prompt is not None
        prompt.content = "B 新提示词"
        await session.commit()
    assert await _load_negative_prompt(project_a.id) == "A 提示词"
    assert await _load_negative_prompt(project_b.id) == "B 提示词"


@pytest.mark.asyncio
async def test_unconfigured_project_caches_empty_value(
    db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """无记录项目也缓存空值（避免每批查库），且不再需要 NegativeExample。"""

    project = await _create_project(db_session_factory, "engine-cache-empty")
    async with db_session_factory() as session:
        session.add(
            NegativeExample(
                rule_id="sql-injection",
                project_id=project.id,
                code_snippet="x",
                explanation="样本",
                approved_at=datetime.now(UTC),
            )
        )
        await session.commit()

    assert await _load_negative_prompt(project.id) == ""
    assert await _load_negative_prompt(project.id) == ""
