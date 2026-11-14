"""模型供应商（LLM provider）配置的 CRUD 路由。

敏感字段 ``api_key`` 入库前由模型层 Fernet 加密，响应一律脱敏为 ``****``；
编辑时"留空则不改"（见 ``ProviderUpdate`` 语义），避免明文回显覆盖真实密钥。
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
from models.provider import Provider
from schemas.provider import ProviderCreate, ProviderRead, ProviderUpdate

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["admin"],
    dependencies=[Depends(_require_admin_auth)],
)


@router.get(
    "/providers",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:providers"))],
)
async def list_providers(
    db: DbSession,
    enabled: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List LLM providers with paging, keyword filtering, and sorting."""

    stmt = select(Provider)
    if enabled is not None:
        stmt = stmt.where(Provider.enabled == enabled)
    if q:
        stmt = stmt.where(Provider.name.ilike(f"%{q}%"))
    return await _paginate(db, stmt, ProviderRead, "providers", sort, limit, offset)


@router.post(
    "/providers",
    response_model=ProviderRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_permission("page:providers"))],
)
async def create_provider(payload: ProviderCreate, db: DbSession) -> ProviderRead:
    """Create an LLM provider configuration."""

    provider = Provider(**payload.model_dump())
    return await _create(db, provider, ProviderRead, "Provider already exists")


@router.get(
    "/providers/{provider_id}",
    response_model=ProviderRead,
    dependencies=[Depends(_require_permission("page:providers"))],
)
async def get_provider(provider_id: UUID, db: DbSession) -> ProviderRead:
    """Return one LLM provider by ID."""

    provider = await _get_or_404(db, Provider, provider_id, "Provider")
    return ProviderRead.model_validate(provider)


@router.patch(
    "/providers/{provider_id}",
    response_model=ProviderRead,
    dependencies=[Depends(_require_permission("page:providers"))],
)
async def update_provider(
    provider_id: UUID,
    payload: ProviderUpdate,
    db: DbSession,
) -> ProviderRead:
    """Update an LLM provider configuration."""

    provider = await _get_or_404(db, Provider, provider_id, "Provider")
    return await _update(db, provider, payload, ProviderRead)


@router.delete(
    "/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_permission("page:providers"))],
)
async def delete_provider(provider_id: UUID, db: DbSession) -> None:
    """Delete an LLM provider configuration."""

    await _delete(db, Provider, provider_id, "Provider")
