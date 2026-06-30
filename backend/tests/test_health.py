"""Tests for the health check endpoint."""

import pytest
from httpx import AsyncClient

from app.api import health


@pytest.mark.asyncio
async def test_health_returns_dependency_status(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health endpoint reports app version and backend dependency status."""

    async def ping_database_ok() -> bool:
        return True

    async def ping_redis_ok() -> bool:
        return True

    monkeypatch.setattr(health, "ping_database", ping_database_ok)
    monkeypatch.setattr(health, "ping_redis", ping_redis_ok)

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "0.1.0-dev",
        "db": "ok",
        "redis": "ok",
    }


@pytest.mark.asyncio
async def test_health_reports_dependency_errors(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health endpoint keeps HTTP 200 while surfacing dependency failures."""

    async def ping_database_error() -> bool:
        return False

    async def ping_redis_error() -> bool:
        return False

    monkeypatch.setattr(health, "ping_database", ping_database_error)
    monkeypatch.setattr(health, "ping_redis", ping_redis_error)

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["db"] == "error"
    assert response.json()["redis"] == "error"
