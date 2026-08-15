"""Tests for user_mappings: repository CRUD + admin API routes.

Repository 层直接跑在测试数据库上（Base.metadata.create_all 建表）；API 层走
``admin_client``（JWT 鉴权 + dependency_overrides 到同一测试库）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core import config, db
from core.db import Base, get_db
from main import create_app
from models.project import Project
from models.user_mapping import UserMapping
from repositories.user_mapping_repository import UserMappingRepository

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


@pytest_asyncio.fixture
async def session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker, None]:
    """Provide a test database session factory with a fresh schema."""

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode("utf-8"))
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()

    test_engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    yield factory

    await test_engine.dispose()
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()


@pytest_asyncio.fixture
async def admin_client(
    session_factory: async_sessionmaker,
) -> AsyncGenerator[AsyncClient, None]:
    """Create an authenticated admin HTTP client bound to the test database."""

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


async def _seed_project(session: AsyncSession) -> Project:
    """Insert a minimal Project row and return it (not committed)."""

    project = Project(
        name=f"proj-{uuid4().hex[:8]}",
        gitlab_project_id=str(uuid4().int % 100000),
        gitlab_access_token="gl-token",
        webhook_secret="hook-secret",
    )
    session.add(project)
    await session.flush()
    return project


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_repository_create_and_get_by_gitlab_username(
    session_factory: async_sessionmaker,
) -> None:
    """create 后能按 (project_id, gitlab_username) 唯一取回映射。"""

    async with session_factory() as session:
        project = await _seed_project(session)
        repo = UserMappingRepository(session)
        await repo.create(
            UserMapping(
                project_id=project.id,
                gitlab_username="alice",
                dingtalk_mobile="13800138000",
                display_name="Alice",
            ),
        )
        await session.commit()

        found = await repo.get_by_gitlab_username(project.id, "alice")
        assert found is not None
        assert found.dingtalk_mobile == "13800138000"
        assert found.display_name == "Alice"
        assert await repo.get_by_gitlab_username(project.id, "nobody") is None


@pytest.mark.asyncio
async def test_repository_list_by_project_scopes_and_orders(
    session_factory: async_sessionmaker,
) -> None:
    """list_by_project 只返回该项目映射，并按 gitlab_username 排序。"""

    async with session_factory() as session:
        project_a = await _seed_project(session)
        project_b = await _seed_project(session)
        repo = UserMappingRepository(session)
        for name in ("carol", "alice", "bob"):
            await repo.create(
                UserMapping(
                    project_id=project_a.id,
                    gitlab_username=name,
                    dingtalk_mobile="13800000000",
                ),
            )
        await repo.create(
            UserMapping(
                project_id=project_b.id,
                gitlab_username="dave",
                dingtalk_mobile="13900000000",
            ),
        )
        await session.commit()

        names = [m.gitlab_username for m in await repo.list_by_project(project_a.id)]
        assert names == ["alice", "bob", "carol"]
        assert [m.gitlab_username for m in await repo.list_by_project(project_b.id)] == ["dave"]


@pytest.mark.asyncio
async def test_repository_update_changes_fields(
    session_factory: async_sessionmaker,
) -> None:
    """update 按字段字典局部更新映射。"""

    async with session_factory() as session:
        project = await _seed_project(session)
        repo = UserMappingRepository(session)
        mapping = await repo.create(
            UserMapping(
                project_id=project.id,
                gitlab_username="alice",
                dingtalk_mobile="13800138000",
            ),
        )
        await repo.update(mapping, {"dingtalk_mobile": "13900139000", "display_name": "Alice Z"})
        await session.commit()

        refreshed = await repo.get_by_gitlab_username(project.id, "alice")
        assert refreshed is not None
        assert refreshed.dingtalk_mobile == "13900139000"
        assert refreshed.display_name == "Alice Z"
        assert refreshed.gitlab_username == "alice"


@pytest.mark.asyncio
async def test_repository_delete_removes_mapping(
    session_factory: async_sessionmaker,
) -> None:
    """delete 后映射不可再查到。"""

    async with session_factory() as session:
        project = await _seed_project(session)
        repo = UserMappingRepository(session)
        mapping = await repo.create(
            UserMapping(
                project_id=project.id,
                gitlab_username="alice",
                dingtalk_mobile="13800138000",
            ),
        )
        await repo.delete(mapping)
        await session.commit()

        assert await repo.get_by_gitlab_username(project.id, "alice") is None


@pytest.mark.asyncio
async def test_repository_duplicate_username_violates_unique_constraint(
    session_factory: async_sessionmaker,
) -> None:
    """同项目重复 gitlab_username 触发唯一约束（映射到 API 层的 409）。"""

    async with session_factory() as session:
        project = await _seed_project(session)
        repo = UserMappingRepository(session)
        await repo.create(
            UserMapping(
                project_id=project.id,
                gitlab_username="alice",
                dingtalk_mobile="13800138000",
            ),
        )
        with pytest.raises(IntegrityError):
            # create 内部 flush 立即触发唯一约束校验。
            await repo.create(
                UserMapping(
                    project_id=project.id,
                    gitlab_username="alice",
                    dingtalk_mobile="13900139000",
                ),
            )


# --------------------------------------------------------------------------- #
# Admin API
# --------------------------------------------------------------------------- #


async def _create_project_via_api(client: AsyncClient) -> str:
    """Create a project through the admin API and return its UUID."""

    response = await client.post(
        "/api/projects",
        json={
            "name": f"mapping-proj-{uuid4().hex[:6]}",
            "gitlab_project_id": f"group/{uuid4().hex[:6]}",
            "gitlab_base_url": "https://gitlab.example.com",
            "gitlab_access_token": "gl-token",
            "webhook_secret": "hook-secret",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


@pytest.mark.asyncio
async def test_user_mapping_api_crud_roundtrip(admin_client: AsyncClient) -> None:
    """创建 -> 列表 -> 更新 -> 删除 全链路，响应字段完整。"""

    project_id = await _create_project_via_api(admin_client)

    create_response = await admin_client.post(
        f"/api/projects/{project_id}/user-mappings",
        json={
            "gitlab_username": "alice",
            "dingtalk_mobile": "13800138000",
            "dingtalk_userid": "dt-user-1",
            "display_name": "Alice",
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()
    mapping_id = created["id"]
    assert created["gitlab_username"] == "alice"
    assert created["dingtalk_mobile"] == "13800138000"
    assert created["dingtalk_userid"] == "dt-user-1"
    assert created["display_name"] == "Alice"
    assert created["created_at"] and created["updated_at"]

    list_response = await admin_client.get(f"/api/projects/{project_id}/user-mappings")
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [mapping_id]

    update_response = await admin_client.put(
        f"/api/user-mappings/{mapping_id}",
        json={"dingtalk_mobile": "13900139000", "display_name": "Alice Z"},
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["dingtalk_mobile"] == "13900139000"
    assert updated["display_name"] == "Alice Z"
    # 未传字段保持原值
    assert updated["gitlab_username"] == "alice"

    delete_response = await admin_client.delete(f"/api/user-mappings/{mapping_id}")
    assert delete_response.status_code == 204

    list_after = await admin_client.get(f"/api/projects/{project_id}/user-mappings")
    assert list_after.json() == []

    missing = await admin_client.put(
        f"/api/user-mappings/{mapping_id}",
        json={"dingtalk_mobile": "13900139000"},
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_user_mapping_api_duplicate_returns_409(admin_client: AsyncClient) -> None:
    """同项目重复 gitlab_username 唯一约束冲突映射为 409。"""

    project_id = await _create_project_via_api(admin_client)
    payload = {"gitlab_username": "bob", "dingtalk_mobile": "13800138000"}

    first = await admin_client.post(f"/api/projects/{project_id}/user-mappings", json=payload)
    assert first.status_code == 201

    second = await admin_client.post(f"/api/projects/{project_id}/user-mappings", json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_user_mapping_api_unknown_project_returns_404(admin_client: AsyncClient) -> None:
    """项目不存在时列表 / 创建均 404。"""

    unknown = uuid4()
    assert (
        await admin_client.get(f"/api/projects/{unknown}/user-mappings")
    ).status_code == 404
    assert (
        await admin_client.post(
            f"/api/projects/{unknown}/user-mappings",
            json={"gitlab_username": "alice", "dingtalk_mobile": "13800138000"},
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_user_mapping_api_requires_auth(admin_client: AsyncClient) -> None:
    """未带 JWT 的请求应被 401 拒绝。"""

    response = await admin_client.get(
        "/api/projects/00000000-0000-0000-0000-000000000000/user-mappings",
        headers={"Authorization": ""},
    )
    assert response.status_code == 401
