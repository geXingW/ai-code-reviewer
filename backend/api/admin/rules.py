"""审查规则库的路由（跨项目共享）。

``rule_id`` 可留空，由 :func:`_generate_rule_slug` 从标题生成唯一 slug
（英文 slugify，含中文时回退 ``rule-<uuid8>``，冲突追加 ``-2`` / ``-3``）。
"""

from __future__ import annotations

import logging
import re
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from models.rule import Rule
from repositories import (
    RuleRepository,
)
from schemas.rule import RuleCreate, RuleRead, RuleUpdate

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["admin"],
    dependencies=[Depends(_require_admin_auth)],
)


@router.get(
    "/rules",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:rules"))],
)
async def list_rules(
    db: DbSession,
    enabled: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List review rules with filters and pagination."""

    stmt = select(Rule)
    if enabled is not None:
        stmt = stmt.where(Rule.enabled == enabled)
    if q:
        stmt = stmt.where((Rule.rule_id.ilike(f"%{q}%")) | (Rule.title.ilike(f"%{q}%")))
    return await _paginate(db, stmt, RuleRead, "rules", sort, limit, offset)


@router.post(
    "/rules",
    response_model=RuleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_permission("page:rules"))],
)
async def create_rule(payload: RuleCreate, db: DbSession) -> RuleRead:
    """Create a reusable review rule."""

    # rule_id 留空时从 title 自动生成 slug；显式传入则沿用（含冲突 -> 409）。
    rule_id = payload.rule_id
    if not rule_id:
        rule_id = await _generate_rule_slug(payload.title, db)
    data = payload.model_dump()
    data["rule_id"] = rule_id
    rule = Rule(**data)
    return await _create(db, rule, RuleRead, "Rule already exists")


@router.get(
    "/rules/{rule_id}",
    response_model=RuleRead,
    dependencies=[Depends(_require_permission("page:rules"))],
)
async def get_rule(rule_id: UUID, db: DbSession) -> RuleRead:
    """Return one review rule by ID."""

    rule = await _get_or_404(db, Rule, rule_id, "Rule")
    return RuleRead.model_validate(rule)


@router.patch(
    "/rules/{rule_id}",
    response_model=RuleRead,
    dependencies=[Depends(_require_permission("page:rules"))],
)
async def update_rule(rule_id: UUID, payload: RuleUpdate, db: DbSession) -> RuleRead:
    """Update a reusable review rule."""

    rule = await _get_or_404(db, Rule, rule_id, "Rule")
    return await _update(db, rule, payload, RuleRead)


@router.delete(
    "/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_permission("page:rules"))],
)
async def delete_rule(rule_id: UUID, db: DbSession) -> None:
    """Delete a reusable review rule."""

    await _delete(db, Rule, rule_id, "Rule")


async def _generate_rule_slug(title: str, db: AsyncSession) -> str:
    """从标题自动生成唯一的 rule_id slug。

    - 标题含中文 -> 用 ``rule-<uuid8>`` 兜底，避免 slug 出现非 ASCII；
    - 否则按非「英文/数字/空格/-」字符清洗后小写、空格转 ``-`` 生成 slug；
    - 清洗后为空 -> 退回 ``rule-<uuid8>``；
    - 与已有 rule_id 冲突 -> 追加 ``-2``、``-3`` 后缀直至唯一。
    """

    if re.search("[一-鿿]", title):
        return f"rule-{uuid4().hex[:8]}"
    slug = re.sub(r"[^\w\s-]", "", title).strip().lower().replace(" ", "-")
    if not slug:
        return f"rule-{uuid4().hex[:8]}"
    repo = RuleRepository(db)
    candidate = slug
    suffix = 2
    while await repo.get_by_rule_id(candidate):
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate
