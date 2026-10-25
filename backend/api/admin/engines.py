"""引擎持久化配置（``engines`` 表）路由。

与 ``/api/engines``（运行时注册表信息、健康检查）不同：这里管理的是数据库里
可被项目引用的引擎配置行。
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from api.admin.common import (
    Page,
    _create,
    _delete,
    _get_or_404,
    _paginate,
    _update,
)
from api.admin.deps import (
    _require_admin_auth,
    _require_permission,
)
from core.db import DbSession
from models.engine import Engine
from schemas.engine import EngineCreate, EngineRead, EngineUpdate

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["admin"],
    dependencies=[Depends(_require_admin_auth)],
)


@router.get(
    "/engines/configs",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:engines"))],
)
async def list_engine_configs(
    db: DbSession,
    enabled: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List persisted engine configurations."""

    stmt = select(Engine)
    if enabled is not None:
        stmt = stmt.where(Engine.enabled == enabled)
    if q:
        stmt = stmt.where(Engine.name.ilike(f"%{q}%"))
    return await _paginate(db, stmt, EngineRead, "engines", sort, limit, offset)


@router.post(
    "/engines/configs",
    response_model=EngineRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_permission("page:engines"))],
)
async def create_engine_config(payload: EngineCreate, db: DbSession) -> EngineRead:
    """Create a persisted engine configuration."""

    engine = Engine(**payload.model_dump())
    return await _create(db, engine, EngineRead, "Engine config already exists")


@router.get(
    "/engines/configs/{engine_id}",
    response_model=EngineRead,
    dependencies=[Depends(_require_permission("page:engines"))],
)
async def get_engine_config(engine_id: UUID, db: DbSession) -> EngineRead:
    """Return one persisted engine configuration."""

    engine = await _get_or_404(db, Engine, engine_id, "Engine config")
    return EngineRead.model_validate(engine)


@router.patch(
    "/engines/configs/{engine_id}",
    response_model=EngineRead,
    dependencies=[Depends(_require_permission("page:engines"))],
)
async def update_engine_config(engine_id: UUID, payload: EngineUpdate, db: DbSession) -> EngineRead:
    """Update a persisted engine configuration, including enable/disable."""

    engine = await _get_or_404(db, Engine, engine_id, "Engine config")
    return await _update(db, engine, payload, EngineRead)


@router.delete(
    "/engines/configs/{engine_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_permission("page:engines"))],
)
async def delete_engine_config(engine_id: UUID, db: DbSession) -> None:
    """Delete a persisted engine configuration."""

    await _delete(db, Engine, engine_id, "Engine config")
