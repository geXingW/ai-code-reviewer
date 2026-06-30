"""End-to-end tests for the /api/engines REST endpoints."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import AsyncClient

from app.engines import (
    Finding,
    HealthStatus,
    ReviewContext,
    ReviewEngine,
    get_engine_registry,
    load_builtin_engines,
)
from app.engines.llm_engine.engine import LLMDirectEngine


class _BoomEngine(ReviewEngine):
    """Engine whose health_check raises — used to assert error handling."""

    def name(self) -> str:
        return "boom"

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        _ = ctx
        return []

    def supports_feedback(self) -> bool:
        return False

    async def health_check(self) -> HealthStatus:
        msg = "intentional health failure"
        raise RuntimeError(msg)


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    """Provide a clean registry per test, then restore built-ins.

    ``load_builtin_engines`` relies on import-time decorators, so once
    a module has been imported, calling it again is a no-op (Python
    caches modules). To get a fresh registry every test, we clear it
    and re-register the built-in engines explicitly.
    """

    registry = get_engine_registry()
    registry.clear()
    # Re-create built-ins explicitly so each test sees a clean state.
    registry.register(LLMDirectEngine())
    # Defensive: also call the builtin loader in case future engines
    # are added that do not rely solely on the LLMDirectEngine import.
    load_builtin_engines()
    yield
    registry.clear()
    registry.register(LLMDirectEngine())


@pytest.mark.asyncio
async def test_list_engines_contains_builtin_llm_direct(client: AsyncClient) -> None:
    """The built-in ``llm-direct`` engine shows up with healthy=True."""

    response = await client.get("/api/engines")
    assert response.status_code == 200

    payload = response.json()
    assert isinstance(payload, list)
    by_name = {item["name"]: item for item in payload}
    assert "llm-direct" in by_name

    entry = by_name["llm-direct"]
    assert entry["supports_feedback"] is True
    assert entry["requires_repo_clone"] is False
    assert entry["healthy"] is True
    assert entry["health_status"] == "ok"


@pytest.mark.asyncio
async def test_engine_health_returns_details(client: AsyncClient) -> None:
    """Single-engine health endpoint mirrors HealthStatus payload."""

    response = await client.get("/api/engines/llm-direct/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["name"] == "llm-direct"
    assert payload["status"] == "ok"
    assert payload["details"]["implementation"] == "llm-direct"
    assert payload["details"]["supports_feedback"] is True


@pytest.mark.asyncio
async def test_engine_health_unknown_returns_404(client: AsyncClient) -> None:
    """Unknown engine names produce a 404 with a helpful message."""

    response = await client.get("/api/engines/does-not-exist/health")
    assert response.status_code == 404
    assert "not registered" in response.json()["detail"]


@pytest.mark.asyncio
async def test_health_check_exceptions_are_isolated(client: AsyncClient) -> None:
    """A broken engine's exception must not poison the listing endpoint."""

    get_engine_registry().register(_BoomEngine())

    response = await client.get("/api/engines")
    assert response.status_code == 200
    by_name = {item["name"]: item for item in response.json()}
    assert by_name["boom"]["healthy"] is False
    assert by_name["boom"]["health_status"] == "error"
    # Built-in engine remains intact.
    assert by_name["llm-direct"]["healthy"] is True


@pytest.mark.asyncio
async def test_single_engine_health_swallows_exceptions(client: AsyncClient) -> None:
    """``GET /engines/{name}/health`` returns status=error on raise."""

    get_engine_registry().register(_BoomEngine())

    response = await client.get("/api/engines/boom/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert "intentional health failure" in (payload["message"] or "")
