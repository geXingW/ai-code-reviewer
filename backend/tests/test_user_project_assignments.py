"""Tests for user-project assignments API (PUT /api/users/{user_id}/projects).

回归背景：更新用户项目分配采用「先删旧、后插新」，若在同一 flush 内执行，
SQLAlchemy 对同一张表先发 INSERT 后发 DELETE，重新分配已持有的项目会触发
uniq_user_project 唯一约束冲突（1062 Duplicate entry）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient

from core import config, db


@pytest_asyncio.fixture
async def admin_client(
    monkeypatch: pytest.MonkeyPatch,
    db_client: AsyncClient,
) -> AsyncClient:
    """db_client 未内置登录，手动认证（凭据来自 settings，不查库）。

    测试用例会创建用户，users 表不再为空，legacy 回退失效，因此显式关闭
    RBAC 走单管理员模式（与 test_user_mapping.py 的隐式前提一致）。
    """
    monkeypatch.setenv("RBAC_ENABLED", "false")
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()

    login_response = await db_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert login_response.status_code == 200
    db_client.headers.update(
        {"Authorization": f"Bearer {login_response.json()['access_token']}"}
    )
    return db_client


async def _create_project(client: AsyncClient) -> str:
    response = await client.post(
        "/api/projects",
        json={
            "name": f"assign-proj-{uuid4().hex[:6]}",
            "gitlab_project_id": f"group/{uuid4().hex[:6]}",
            "gitlab_base_url": "https://gitlab.example.com",
            "gitlab_access_token": "gl-token",
            "webhook_secret": "hook-secret",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _create_user(client: AsyncClient) -> str:
    response = await client.post(
        "/api/users",
        json={"username": f"user-{uuid4().hex[:8]}", "password": "password-123"},
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.asyncio
async def test_reassign_kept_project_succeeds(admin_client: AsyncClient) -> None:
    """重复分配已持有的项目不应触发唯一约束冲突。"""

    user_id = await _create_user(admin_client)
    project_a = await _create_project(admin_client)
    project_b = await _create_project(admin_client)

    first = await admin_client.put(
        f"/api/users/{user_id}/projects",
        json={"project_ids": [project_a]},
    )
    assert first.status_code == 200
    assert first.json()["project_ids"] == [project_a]

    # 保留 project_a 并追加 project_b（修复前此处 500）
    second = await admin_client.put(
        f"/api/users/{user_id}/projects",
        json={"project_ids": [project_a, project_b]},
    )
    assert second.status_code == 200
    assert sorted(second.json()["project_ids"]) == sorted([project_a, project_b])

    detail = await admin_client.get(f"/api/users/{user_id}")
    assert sorted(detail.json()["project_ids"]) == sorted([project_a, project_b])


@pytest.mark.asyncio
async def test_unassign_all_projects(admin_client: AsyncClient) -> None:
    """空 project_ids 清空全部分配。"""

    user_id = await _create_user(admin_client)
    project_a = await _create_project(admin_client)

    assigned = await admin_client.put(
        f"/api/users/{user_id}/projects",
        json={"project_ids": [project_a]},
    )
    assert assigned.status_code == 200

    cleared = await admin_client.put(
        f"/api/users/{user_id}/projects",
        json={"project_ids": []},
    )
    assert cleared.status_code == 200
    assert cleared.json()["project_ids"] == []

    detail = await admin_client.get(f"/api/users/{user_id}")
    assert detail.json()["project_ids"] == []


@pytest.mark.asyncio
async def test_assign_unknown_user_returns_404(admin_client: AsyncClient) -> None:
    """用户不存在时返回 404。"""

    response = await admin_client.put(
        f"/api/users/{uuid4()}/projects",
        json={"project_ids": []},
    )
    assert response.status_code == 404
