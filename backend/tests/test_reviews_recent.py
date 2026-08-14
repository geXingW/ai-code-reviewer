"""Tests for the DB-backed /api/reviews/recent endpoint (Issue #76)."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import reviews as reviews_api
from core import config, db
from core.db import Base, get_db
from main import create_app
from models.finding import Finding
from models.project import Project
from models.review import Review

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


@pytest_asyncio.fixture
async def db_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[AsyncClient, async_sessionmaker[AsyncSession]], None]:
    """带 DB 覆盖的 FastAPI 客户端 + session_factory，供 recent 用例种子数据。"""

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")
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
        # 登录获取 admin JWT
        login_response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        if login_response.status_code == 200:
            token = login_response.json()["access_token"]
            client.headers.update({"Authorization": f"Bearer {token}"})
        yield client, session_factory

    app.dependency_overrides.clear()
    await test_engine.dispose()
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_recent_reviews_reads_from_db(
    db_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """/api/reviews/recent 应直接从 DB 读，返回 engine_used/created_at/项目名/BLOCKER 计数。"""

    client, session_factory = db_client
    # 清空内存 deque，确保断言的是 DB 路径而不是回退。
    reviews_api.clear_recent_reviews_for_tests()

    async with session_factory() as session:
        project = Project(
            name="示例项目",
            gitlab_project_id="99",
            gitlab_access_token="tok",
            webhook_secret="sec",
        )
        session.add(project)
        await session.flush()

        review = Review(
            project_id=project.id,
            mr_iid="42",
            source_branch="feature/demo",
            target_branch="master",
            commit_sha="deadbeefcafebabe",
            status="done",
            engine_used="llm-direct",
            has_blocker=True,
            finding_count=3,
        )
        session.add(review)
        await session.flush()

        session.add_all(
            [
                Finding(
                    review_id=review.id,
                    file_path="app/a.py",
                    line_number=1,
                    rule_id="R-1",
                    severity="BLOCKER",
                    title="阻断问题",
                ),
                Finding(
                    review_id=review.id,
                    file_path="app/b.py",
                    line_number=2,
                    rule_id="R-2",
                    severity="WARNING",
                    title="警告问题",
                ),
            ]
        )
        await session.commit()

    response = await client.get("/api/reviews/recent")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    item = body[0]
    assert item["review_id"] is not None
    assert item["engine_used"] == "llm-direct"
    assert item["created_at"] is not None
    assert item["project_path"] == "示例项目"
    assert item["project_id"] == 99
    assert item["mr_iid"] == 42
    assert item["title"] == "MR !42"
    assert item["status"] == "done"
    assert item["has_blocker"] is True
    assert item["finding_count"] == 3
    assert item["blocker_count"] == 1


@pytest.mark.asyncio
async def test_recent_reviews_rejects_missing_auth(
    db_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """Dashboard recent reviews endpoint 受 admin JWT 保护，缺 token 返回 401。"""

    client, _ = db_client
    # 临时去掉 Authorization header
    original_auth = client.headers.get("Authorization")
    client.headers.pop("Authorization", None)
    try:
        response = await client.get("/api/reviews/recent")
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid admin token"
    finally:
        if original_auth:
            client.headers["Authorization"] = original_auth
