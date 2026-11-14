"""管理后台路由共用的响应包装与薄封装。

- :class:`Page`：列表接口的统一信封（``items`` / ``total`` / ``limit`` / ``offset``）。
- :func:`_paginate`：count + 白名单排序 + offset/limit，接受调用方自建的 ``select``
  （列表接口的 join / eager-load 差异太大，不适合在仓储层穷举）。
- :func:`_create` / :func:`_update` / :func:`_delete` / :func:`_get_or_404`：
  单实体 CRUD 的样板收敛，错误映射统一走 :func:`_commit_or_400`
  （唯一约束冲突 → 409，其它数据库错误 → 500）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar, cast
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, asc, desc, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import Base
from models.project import Project
from repositories import (
    ProjectRepository,
)

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=Base)


SchemaT = TypeVar("SchemaT", bound=BaseModel)


_ALLOWED_SORTS: dict[str, set[str]] = {
    "providers": {"created_at", "updated_at", "name", "enabled"},
    "rules": {"created_at", "updated_at", "rule_id", "title", "enabled"},
    "projects": {"created_at", "updated_at", "name", "enabled"},
    "reviews": {"created_at", "updated_at", "status", "mr_iid", "finding_count"},
    "findings": {"created_at", "updated_at", "severity", "file_path", "fp_status"},
    "negative_examples": {"created_at", "updated_at", "rule_id"},
    "engines": {"created_at", "updated_at", "name", "enabled"},
    "users": {"created_at", "updated_at", "username"},
}


class Page(BaseModel):
    """Generic paginated response envelope for admin list endpoints."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[Any]
    total: int
    limit: int
    offset: int


async def _paginate(
    db: AsyncSession,
    stmt: Select[tuple[ModelT]],
    schema: type[SchemaT],
    sort_group: str,
    sort: str,
    limit: int,
    offset: int,
    enrich: Callable[[ModelT], SchemaT] | None = None,
) -> Page:
    """Apply count, sorting, offset, and limit to a select statement.

    直接接收 ``select`` 语句而不是"实体 + 过滤条件"：各接口需要自定义
    join / eager-load / where 组合，在仓储层穷举反而更糟。仓储层保留给
    带业务语义的查询（如 ``ReviewRepository.list_recent``）。

    ``enrich`` 可选：对每行 ORM 对象做自定义封装（如 Review 需附带
    project_name / rules_used），不传则走 ``schema.model_validate`` 默认映射。
    """

    ordered_stmt = _apply_sort(stmt, sort_group, sort)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    result = await db.execute(ordered_stmt.offset(offset).limit(limit))
    items = [
        (enrich(row) if enrich else schema.model_validate(row))
        for row in result.scalars().unique().all()
    ]
    return Page(items=items, total=total or 0, limit=limit, offset=offset)


async def _create(
    db: AsyncSession,
    model: ModelT,
    schema: type[SchemaT],
    error_message: str,
) -> SchemaT:
    """Persist one ORM object and convert it to a response schema.

    commit 统一交给 :func:`_commit_or_400`，错误映射
    （IntegrityError→409、其它 SQLAlchemyError→500）与其它写接口保持一致。
    """

    db.add(model)
    await _commit_or_400(db, error_message)
    await db.refresh(model)
    return schema.model_validate(model)


async def _update(
    db: AsyncSession,
    model: ModelT,
    payload: BaseModel,
    schema: type[SchemaT],
) -> SchemaT:
    """Patch an ORM object from a Pydantic payload."""

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(model, field, value)
    await _commit_or_400(db, f"{model.__class__.__name__} update failed")
    await db.refresh(model)
    return schema.model_validate(model)


async def _delete(db: AsyncSession, model_type: type[ModelT], model_id: UUID, label: str) -> None:
    """Delete an ORM object by primary key or return 404."""

    model = await _get_or_404(db, model_type, model_id, label)
    await db.delete(model)
    await _commit_or_400(db, f"{label} delete failed")


async def _get_or_404(
    db: AsyncSession,
    model_type: type[ModelT],
    model_id: UUID,
    label: str,
    *,
    options: bool = False,
) -> ModelT:
    """Fetch an ORM object by ID or raise a 404 response.

    Project 类型且要求带 relations 时，走 ``ProjectRepository.get_with_relations``
    以复用 eager-load 语句；其它模型走 ``AsyncSession.get``（按主键直取）。
    """

    model: Base | None
    if model_type is Project and options:
        model = await ProjectRepository(db).get_with_relations(model_id)
    else:
        model = await db.get(model_type, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return cast(ModelT, model)


async def _commit_or_400(db: AsyncSession, detail: str) -> None:
    """提交数据库变更，并把持久化失败映射到语义清晰的 HTTP 错误。

    - IntegrityError：唯一约束 / 外键冲突 → 409 Conflict，保留传入的 detail（这就是重名/外键冲突）。
    - 其他 SQLAlchemyError（如加密失败包成的 StatementError）→ 500 Internal Server Error，
      detail 附带根本异常类名与摘要，便于排查，而不是误报成"已存在"。
    """

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        # DBAPIError 有 orig 属性指向底层驱动异常；其它 SQLAlchemyError 没有 orig。
        # 用 getattr 兼容两种情况，并对 mypy 友好。
        orig = getattr(exc, "orig", None)
        internal_detail = (
            f"{detail} (internal error: {exc.__class__.__name__}: "
            f"{str(orig or exc)[:200]})"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=internal_detail,
        ) from exc


def _apply_sort(stmt: Select[tuple[ModelT]], sort_group: str, sort: str) -> Select[tuple[ModelT]]:
    """Apply allow-listed sorting to a query."""

    descending = sort.startswith("-")
    field_name = sort[1:] if descending else sort
    allowed = _ALLOWED_SORTS.get(sort_group, set())
    if field_name not in allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid sort field")
    entity = stmt.column_descriptions[0].get("entity")
    if entity is None or not hasattr(entity, field_name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid sort field")
    column = getattr(entity, field_name)
    return stmt.order_by(desc(column) if descending else asc(column))
