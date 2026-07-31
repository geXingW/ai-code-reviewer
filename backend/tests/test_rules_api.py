"""Tests for the shared review rule admin API, focused on the ``tags`` field.

覆盖：
- 创建规则不传 tags -> 默认空列表；
- 创建规则传 tags -> 正确落库并回显；
- 更新规则 tags -> 正确更新；
- 列表接口返回 tags 字段。

复用 ``test_api_crud.py`` 里 ``admin_client`` 的同款内存库 + 登录装配，保持
每个测试文件自包含（与 conftest 的 ``client`` / ``test_api_crud`` 的
``admin_client`` 各自定义 fixture 的既有风格一致）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config, db
from app.core.db import Base, get_db
from app.main import create_app

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


@pytest_asyncio.fixture
async def admin_client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    """Create an isolated FastAPI client backed by a fresh database."""

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode("utf-8"))
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()
    test_engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        login_response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        assert login_response.status_code == 200
        client.headers.update({"Authorization": f"Bearer {login_response.json()['access_token']}"})
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_create_rule_without_tags_defaults_to_empty_list(
    admin_client: AsyncClient,
) -> None:
    """创建规则时不传 tags -> 默认空列表。"""

    response = await admin_client.post(
        "/api/rules",
        json={
            "rule_id": "test.no-tags",
            "title": "无标签规则",
            "prompt_snippet": "说明片段",
        },
    )
    assert response.status_code == 201
    assert response.json()["tags"] == []


@pytest.mark.asyncio
async def test_create_rule_with_tags_persists(admin_client: AsyncClient) -> None:
    """创建规则时传 tags -> 正确落库并回显。"""

    response = await admin_client.post(
        "/api/rules",
        json={
            "rule_id": "test.with-tags",
            "title": "带标签规则",
            "prompt_snippet": "说明片段",
            "tags": ["security", "python"],
        },
    )
    assert response.status_code == 201
    assert response.json()["tags"] == ["security", "python"]


@pytest.mark.asyncio
async def test_update_rule_tags(admin_client: AsyncClient) -> None:
    """更新规则的 tags -> 正确更新。"""

    create_response = await admin_client.post(
        "/api/rules",
        json={
            "rule_id": "test.update-tags",
            "title": "更新标签规则",
            "prompt_snippet": "说明片段",
            "tags": ["security"],
        },
    )
    assert create_response.status_code == 201
    rule_id = create_response.json()["id"]

    update_response = await admin_client.patch(
        f"/api/rules/{rule_id}",
        json={"tags": ["security", "performance"]},
    )
    assert update_response.status_code == 200
    assert update_response.json()["tags"] == ["security", "performance"]


@pytest.mark.asyncio
async def test_list_rules_returns_tags_field(admin_client: AsyncClient) -> None:
    """列表接口返回 tags 字段。"""

    await admin_client.post(
        "/api/rules",
        json={
            "rule_id": "test.list-tags",
            "title": "列表标签规则",
            "prompt_snippet": "说明片段",
            "tags": ["security"],
        },
    )
    response = await admin_client.get("/api/rules")
    assert response.status_code == 200
    items = response.json()["items"]
    matched = [rule for rule in items if rule["rule_id"] == "test.list-tags"]
    assert matched
    assert matched[0]["tags"] == ["security"]
