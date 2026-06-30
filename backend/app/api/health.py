"""Health check API endpoints."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.db import ping_database
from app.core.redis import ping_redis

router = APIRouter(tags=["health"])
HealthState = Literal["ok", "error"]


class HealthResponse(BaseModel):
    """Response schema for the service health endpoint."""

    status: Literal["ok"]
    version: str
    db: HealthState
    redis: HealthState


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return service, database, and Redis health.

    Returns:
        HealthResponse: Current service health status.
    """

    settings = get_settings()
    db_ok = await ping_database()
    redis_ok = await ping_redis()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        db="ok" if db_ok else "error",
        redis="ok" if redis_ok else "error",
    )
