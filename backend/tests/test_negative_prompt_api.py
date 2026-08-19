"""项目级负样本提示词端点测试（GET / PUT / POST generate）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.negative_example import NegativeExample
from models.project import Project
from models.project_negative_prompt import ProjectNegativePrompt
from models.provider import Provider


async def _login(client: AsyncClient) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _create_project(
    db_session_factory: async_sessionmaker[AsyncSession],
    name: str,
) -> Project:
    """在测试库里建一个项目并返回（含已刷新的主键）。"""

    async with db_session_factory() as session:
        project = Project(
            name=name,
            gitlab_project_id=str(uuid4().int % 10_000_000),
            gitlab_access_token="token",
            webhook_secret="secret",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


@pytest.mark.asyncio
async def test_get_returns_empty_when_unset(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """未配置的项目 GET 返回空字符串 + example_count=0。"""

    project = await _create_project(db_session_factory, "proj-neg-empty")
    headers = await _login(db_client)
    response = await db_client.get(f"/api/projects/{project.id}/negative-prompt", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"content": "", "example_count": 0}


@pytest.mark.asyncio
async def test_get_returns_saved_value_and_count(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """配置过的项目 GET 返回保存值；example_count 只统计该项目 approved 负样本。"""

    project_a = await _create_project(db_session_factory, "proj-neg-a")
    project_b = await _create_project(db_session_factory, "proj-neg-b")

    async with db_session_factory() as session:
        session.add(
            ProjectNegativePrompt(
                project_id=project_a.id,
                content="## 规则：sql-injection 的误报模式",
            )
        )
        # 项目 A：2 条已批准 + 1 条未批准；项目 B：1 条已批准；全局（无项目）：1 条已批准。
        for index in range(2):
            session.add(
                NegativeExample(
                    rule_id="sql-injection",
                    project_id=project_a.id,
                    code_snippet=f"cursor.execute(f'SELECT {index}')",
                    explanation="已白名单校验",
                    approved_at=datetime.now(UTC),
                )
            )
        session.add(
            NegativeExample(
                rule_id="null-check",
                project_id=project_a.id,
                code_snippet="foo.bar()",
                explanation="未批准",
                approved_at=None,
            )
        )
        session.add(
            NegativeExample(
                rule_id="xss",
                project_id=project_b.id,
                code_snippet="v-html=\"x\"",
                explanation="B 项目的样本",
                approved_at=datetime.now(UTC),
            )
        )
        session.add(
            NegativeExample(
                rule_id="global-rule",
                project_id=None,
                code_snippet="legacy()",
                explanation="全局负例",
                approved_at=datetime.now(UTC),
            )
        )
        await session.commit()

    headers = await _login(db_client)
    response = await db_client.get(f"/api/projects/{project_a.id}/negative-prompt", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "## 规则：sql-injection 的误报模式"
    assert body["example_count"] == 2

    response_b = await db_client.get(
        f"/api/projects/{project_b.id}/negative-prompt", headers=headers
    )
    assert response_b.status_code == 200
    assert response_b.json()["example_count"] == 1


@pytest.mark.asyncio
async def test_put_upserts_single_row(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """PUT 首次 INSERT，二次 UPDATE 同一行（不产生多条记录）。"""

    project = await _create_project(db_session_factory, "proj-neg-put")
    headers = await _login(db_client)

    first = await db_client.put(
        f"/api/projects/{project.id}/negative-prompt",
        json={"content": "第一版"},
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json() == {"content": "第一版", "example_count": 0}

    second = await db_client.put(
        f"/api/projects/{project.id}/negative-prompt",
        json={"content": "第二版"},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["content"] == "第二版"

    async with db_session_factory() as session:
        rows = (
            (await session.execute(select(ProjectNegativePrompt))).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].content == "第二版"


@pytest.mark.asyncio
async def test_project_not_found_maps_to_404(db_client: AsyncClient) -> None:
    """GET / PUT / generate 项目不存在 -> 404。"""

    headers = await _login(db_client)
    missing_id = uuid4()
    assert (
        await db_client.get(f"/api/projects/{missing_id}/negative-prompt", headers=headers)
    ).status_code == 404
    assert (
        await db_client.put(
            f"/api/projects/{missing_id}/negative-prompt",
            json={"content": ""},
            headers=headers,
        )
    ).status_code == 404
    assert (
        await db_client.post(
            f"/api/projects/{missing_id}/negative-prompt/generate",
            json={},
            headers=headers,
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_put_content_over_limit_maps_to_422(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """PUT content 超 50000 字符 -> 422。"""

    project = await _create_project(db_session_factory, "proj-neg-too-long")
    headers = await _login(db_client)
    response = await db_client.put(
        f"/api/projects/{project.id}/negative-prompt",
        json={"content": "x" * 50001},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_generate_empty_project_library(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """该项目负样本为空（即使别的项目有样本）-> 400。"""

    project_a = await _create_project(db_session_factory, "proj-neg-gen-empty")
    project_b = await _create_project(db_session_factory, "proj-neg-gen-other")
    async with db_session_factory() as session:
        session.add(
            NegativeExample(
                rule_id="xss",
                project_id=project_b.id,
                code_snippet="v-html=\"x\"",
                explanation="B 项目的样本",
                approved_at=datetime.now(UTC),
            )
        )
        await session.commit()

    headers = await _login(db_client)
    response = await db_client.post(
        f"/api/projects/{project_a.id}/negative-prompt/generate", json={}, headers=headers
    )
    assert response.status_code == 400
    assert "该项目负样本库为空" in response.json()["detail"]


@pytest.mark.asyncio
async def test_generate_no_provider(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """有负样本但无启用 provider 时返回 400。"""

    project = await _create_project(db_session_factory, "proj-neg-no-provider")
    async with db_session_factory() as session:
        session.add(
            NegativeExample(
                rule_id="sql-injection",
                project_id=project.id,
                code_snippet="cursor.execute(f'SELECT {x}')",
                explanation="f-string 参数已被上游白名单校验",
                approved_at=datetime.now(UTC),
            )
        )
        await session.commit()

    headers = await _login(db_client)
    response = await db_client.post(
        f"/api/projects/{project.id}/negative-prompt/generate", json={}, headers=headers
    )
    assert response.status_code == 400
    assert "无可用的 LLM Provider" in response.json()["detail"]


@pytest.mark.asyncio
async def test_generate_success_filters_by_project(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """正常生成：只取该项目的负样本进 prompt（其他项目的样本不能出现）。"""

    project_a = await _create_project(db_session_factory, "proj-neg-gen-a")
    project_b = await _create_project(db_session_factory, "proj-neg-gen-b")
    async with db_session_factory() as session:
        session.add(
            Provider(
                name="test-provider-neg",
                protocol="openai_compatible",
                base_url="https://example.com",
                api_key="sk-test",
                model="gpt-test",
                enabled=True,
            )
        )
        session.add(
            NegativeExample(
                rule_id="sql-injection",
                project_id=project_a.id,
                code_snippet="cursor.execute(f'SELECT FROM a')",
                explanation="A 项目的样本",
                approved_at=datetime.now(UTC),
            )
        )
        session.add(
            NegativeExample(
                rule_id="xss",
                project_id=project_b.id,
                code_snippet="PROJECT_B_UNIQUE_SNIPPET_v-html",
                explanation="B 项目的样本",
                approved_at=datetime.now(UTC),
            )
        )
        # project_id 为 NULL 的全局负例也不进该项目 prompt。
        session.add(
            NegativeExample(
                rule_id="global-rule",
                project_id=None,
                code_snippet="GLOBAL_UNIQUE_SNIPPET_legacy",
                explanation="全局负例",
                approved_at=datetime.now(UTC),
            )
        )
        await session.commit()

    fake_response: Any = type(
        "R", (), {"content": "## 规则：sql-injection\n- 已白名单校验的拼接"}
    )()
    with patch("llm.build_provider") as build:
        build.return_value.chat = AsyncMock(return_value=fake_response)
        headers = await _login(db_client)
        response = await db_client.post(
            f"/api/projects/{project_a.id}/negative-prompt/generate", json={}, headers=headers
        )
        # LLM 输入校验：只含 A 项目的样本，B 项目 / 全局负例被过滤。
        chat_call = build.return_value.chat.await_args
        assert chat_call is not None
        user_prompt = chat_call.args[0][1].content
        assert "SELECT FROM a" in user_prompt
        assert "PROJECT_B_UNIQUE_SNIPPET" not in user_prompt
        assert "GLOBAL_UNIQUE_SNIPPET" not in user_prompt

    assert response.status_code == 200
    body = response.json()
    assert body["source_count"] == 1
    assert "sql-injection" in body["content"]


@pytest.mark.asyncio
async def test_generate_failure_maps_to_500(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """LLM 调用失败映射为 500。"""

    from llm import LLMError

    project = await _create_project(db_session_factory, "proj-neg-fail")
    async with db_session_factory() as session:
        session.add(
            Provider(
                name="test-provider-neg2",
                protocol="openai_compatible",
                base_url="https://example.com",
                api_key="sk-test",
                model="gpt-test",
                enabled=True,
            )
        )
        session.add(
            NegativeExample(
                rule_id="null-check",
                project_id=project.id,
                code_snippet="foo.bar()",
                explanation=None,
                approved_at=datetime.now(UTC),
            )
        )
        await session.commit()

    with patch("llm.build_provider") as build:
        build.return_value.chat = AsyncMock(side_effect=LLMError("upstream down"))
        headers = await _login(db_client)
        response = await db_client.post(
            f"/api/projects/{project.id}/negative-prompt/generate", json={}, headers=headers
        )
    assert response.status_code == 500
    assert "负样本提示词生成失败" in response.json()["detail"]


@pytest.mark.asyncio
async def test_legacy_global_endpoints_removed(db_client: AsyncClient) -> None:
    """旧全局负样本端点已删除 -> 404。"""

    headers = await _login(db_client)
    assert (
        await db_client.get("/api/settings/negative-prompt", headers=headers)
    ).status_code == 404
