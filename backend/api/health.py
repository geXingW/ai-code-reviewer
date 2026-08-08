"""Health check API endpoints."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from core.config import get_settings
from core.db import ping_database

router = APIRouter(tags=["health"])
HealthState = Literal["ok", "error"]


class HealthResponse(BaseModel):
    """Response schema for the service health endpoint."""

    status: Literal["ok"]
    version: str
    db: HealthState


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return service and database health.

    Returns:
        HealthResponse: Current service health status.
    """

    settings = get_settings()
    db_ok = await ping_database()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        db="ok" if db_ok else "error",
    )
