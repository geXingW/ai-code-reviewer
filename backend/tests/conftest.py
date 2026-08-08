"""Shared pytest fixtures for backend tests."""

# ruff: noqa: E402
from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from pathlib import Path

# Add backend/ to sys.path so flat modules are importable without pip install.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client bound to the FastAPI app.

    Yields:
        AsyncClient: HTTPX client using ASGI transport.
    """

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
