"""Shared pytest fixtures for backend tests."""

# ruff: noqa: E402
from __future__ import annotations

import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

# Add backend/ to sys.path so flat modules are importable without pip install.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core import config, db
from core.db import Base, get_db
from main import app as default_app
from main import create_app

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client bound to the default FastAPI app.

    Uses the module-level ``app`` from ``main.py`` with no DB overrides.
    Suitable for tests that mock DB dependencies (e.g. health check with
    patched ``ping_database``).

    For tests that need a real database, request the ``db_client`` fixture
    instead, or request both ``db_session_factory`` and ``client`` — but
    prefer ``db_client`` for clarity.
    """

    transport = ASGITransport(default_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


@pytest_asyncio.fixture
async def db_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker, None]:
    """Provide a test database session factory with a fresh schema.

    Sets up a test engine, creates all tables, and patches the module-level
    ``AsyncSessionLocal`` / ``engine`` in ``core.db`` so code that imports
    those directly (e.g. webhook handlers) uses the test database instead
    of the production one.
    """

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode("utf-8"))
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()

    test_engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    # 替换全局 AsyncSessionLocal / engine，让直接引用的模块也走测试库。
    original_engine = db.engine
    original_session_local = db.AsyncSessionLocal
    db.engine = test_engine  # type: ignore[assignment]
    db.AsyncSessionLocal = session_factory  # type: ignore[assignment]

    yield session_factory

    # 还原
    db.engine = original_engine  # type: ignore[assignment]
    db.AsyncSessionLocal = original_session_local  # type: ignore[assignment]
    await test_engine.dispose()
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()


@pytest_asyncio.fixture
async def db_client(
    db_session_factory: async_sessionmaker,
) -> AsyncGenerator[AsyncClient, None]:
    """Create an HTTP client bound to an app that uses the test database.

    Overrides the ``get_db`` dependency so FastAPI routes use the test
    session factory. Code that uses ``AsyncSessionLocal`` directly also
    uses the test database because ``db_session_factory`` patches it.
    """

    async def override_get_db() -> AsyncGenerator:
        async with db_session_factory() as session:
            yield session

    test_app = create_app()
    test_app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    test_app.dependency_overrides.clear()
