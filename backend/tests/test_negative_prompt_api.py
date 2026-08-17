"""负样本提示词端点测试（GET / PUT / POST generate）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.negative_example import NegativeExample
from models.provider import Provider


async def _login(client: AsyncClient) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_negative_prompt_get_returns_empty_when_unset(db_client: AsyncClient) -> None:
    """未设置时 GET 返回空字符串。"""

    headers = await _login(db_client)
    response = await db_client.get("/api/settings/negative-prompt", headers=headers)
    assert response.status_code == 200
    assert response.json()["content"] == ""


@pytest.mark.asyncio
async def test_negative_prompt_generate_empty_library(db_client: AsyncClient) -> None:
    """负样本库为空时返回 400。"""

    headers = await _login(db_client)
    response = await db_client.post(
        "/api/settings/negative-prompt/generate", json={}, headers=headers
    )
    assert response.status_code == 400
    assert "负样本库为空" in response.json()["detail"]


@pytest.mark.asyncio
async def test_negative_prompt_generate_no_provider(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """有负样本但无启用 provider 时返回 400。"""

    async with db_session_factory() as session:
        session.add(
            NegativeExample(
                rule_id="sql-injection",
                code_snippet="cursor.execute(f'SELECT {x}')",
                explanation="f-string 参数已被上游白名单校验",
                approved_at=datetime.now(UTC),
            )
        )
        await session.commit()

    headers = await _login(db_client)
    response = await db_client.post(
        "/api/settings/negative-prompt/generate", json={}, headers=headers
    )
    assert response.status_code == 400
    assert "无可用的 LLM Provider" in response.json()["detail"]


@pytest.mark.asyncio
async def test_negative_prompt_generate_success(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """正常生成：mock 掉 LLM provider，返回 content 与 source_count。"""

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
                code_snippet="cursor.execute(f'SELECT {x}')",
                explanation="f-string 参数已被上游白名单校验",
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
            "/api/settings/negative-prompt/generate", json={}, headers=headers
        )
    assert response.status_code == 200
    body = response.json()
    assert body["source_count"] == 1
    assert "sql-injection" in body["content"]


@pytest.mark.asyncio
async def test_negative_prompt_generate_failure_maps_to_500(
    db_client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """LLM 调用失败映射为 500。"""

    from llm import LLMError

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
            "/api/settings/negative-prompt/generate", json={}, headers=headers
        )
    assert response.status_code == 500
    assert "负样本提示词生成失败" in response.json()["detail"]
