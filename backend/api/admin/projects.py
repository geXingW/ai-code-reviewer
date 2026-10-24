"""GitLab 项目配置及其附属资源的路由。

覆盖三类资源：项目本体（含嵌套 rules / block policies 的读写）、项目级通知渠道、
项目成员映射。写项目时 ``rules=None`` 会走"安全默认"：自动关联所有 BLOCKER 级规则。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.admin.common import (
    Page,
    _commit_or_400,
    _create,
    _delete,
    _get_or_404,
    _paginate,
)
from api.admin.deps import (
    _require_admin_auth,
    _require_permission,
)
from core.config import get_settings
from core.db import DbSession
from models.project import Project
from models.project_block_policy import ProjectBlockPolicy
from models.project_notification_channel import ProjectNotificationChannel
from models.project_rule import ProjectRule
from models.rule import Rule
from models.user_mapping import UserMapping
from repositories import (
    ProjectNotificationChannelRepository,
    UserMappingRepository,
)
from schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from schemas.project_block_policy import ProjectBlockPolicyCreate
from schemas.project_notification_channel import (
    ProjectNotificationChannelCreate,
    ProjectNotificationChannelRead,
    ProjectNotificationChannelUpdate,
)
from schemas.project_rule import ProjectRuleCreate
from schemas.user_mapping import (
    UserMappingCreate,
    UserMappingResponse,
    UserMappingUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["admin"],
    dependencies=[Depends(_require_admin_auth)],
)


@router.get(
    "/projects",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def list_projects(
    db: DbSession,
    request: Request,
    enabled: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List GitLab project configurations."""

    stmt = _project_select()
    project_ids = getattr(request.state, "project_ids", [])
    # 非超级管理员：只返回分配给该用户的项目。
    if project_ids:
        stmt = stmt.where(Project.id.in_(project_ids))
    if enabled is not None:
        stmt = stmt.where(Project.enabled == enabled)
    if q:
        stmt = stmt.where(
            (Project.name.ilike(f"%{q}%")) | (Project.gitlab_project_id.ilike(f"%{q}%")),
        )
    return await _paginate(db, stmt, ProjectRead, "projects", sort, limit, offset)


@router.post(
    "/projects",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def create_project(payload: ProjectCreate, db: DbSession) -> ProjectRead:
    """Create a project with optional nested rules and block policies.

    「安全默认」策略：``payload.rules is None`` 表示前端未显式传规则关联，
    此时后端自动把所有 ``enabled=true && severity_default=BLOCKER`` 的规则关联
    到新项目上——保证新项目开箱就有基础安全规则在跑。显式传 ``[]``
    代表用户 opt out，不做自动关联。显式传非空数组则以传入内容为准。
    """

    data = payload.model_dump(exclude={"rules", "block_policies"})
    # gitlab_base_url 未传或为空时，兜底到全局 settings.gitlab_base_url
    if not data.get("gitlab_base_url"):
        data["gitlab_base_url"] = get_settings().gitlab_base_url
    project = Project(**data)
    if payload.rules is None:
        rules = await _default_blocker_project_rules(db)
    else:
        rules = list(payload.rules)
    _replace_project_rules(project, rules)
    _replace_block_policies(project, payload.block_policies or [])
    db.add(project)
    await _commit_or_400(db, "Project create failed")
    await db.refresh(project)
    return await _load_project_read(db, project.id)


@router.get(
    "/projects/{project_id}",
    response_model=ProjectRead,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def get_project(project_id: UUID, db: DbSession) -> ProjectRead:
    """Return one project configuration by ID."""

    return await _load_project_read(db, project_id)


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectRead,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def update_project(project_id: UUID, payload: ProjectUpdate, db: DbSession) -> ProjectRead:
    """Update a project and optionally replace nested rules and block policies."""

    project = await _get_or_404(db, Project, project_id, "Project", options=True)
    data = payload.model_dump(exclude_unset=True, exclude={"rules", "block_policies"})
    # 显式传了 gitlab_base_url 但为空串时，也兜底到全局默认
    if "gitlab_base_url" in data and not data["gitlab_base_url"]:
        data["gitlab_base_url"] = get_settings().gitlab_base_url
    # 密钥字段（gitlab_access_token / webhook_secret）为空字符串时跳过更新，
    # 避免覆盖数据库中已存的密钥。前端编辑模式留空表示"不修改"。
    for _secret_field in ("gitlab_access_token", "webhook_secret"):
        if _secret_field in data and not data[_secret_field]:
            del data[_secret_field]
    for field, value in data.items():
        setattr(project, field, value)
    if payload.rules is not None:
        _replace_project_rules(project, payload.rules)
    if payload.block_policies is not None:
        _replace_block_policies(project, payload.block_policies)
    await _commit_or_400(db, "Project update failed")
    return await _load_project_read(db, project_id)


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def delete_project(project_id: UUID, db: DbSession) -> None:
    """Delete a project configuration and dependent rules/policies/reviews."""

    await _delete(db, Project, project_id, "Project")


@router.get(
    "/projects/{project_id}/notification-channels",
    response_model=list[ProjectNotificationChannelRead],
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def list_project_notification_channels(
    project_id: UUID,
    db: DbSession,
) -> list[ProjectNotificationChannelRead]:
    """List notification channels (e.g. DingTalk webhooks) configured for a project."""

    await _get_or_404(db, Project, project_id, "Project")
    repo = ProjectNotificationChannelRepository(db)
    channels = await repo.get_by_project(project_id)
    return [ProjectNotificationChannelRead.model_validate(channel) for channel in channels]


@router.post(
    "/projects/{project_id}/notification-channels",
    response_model=ProjectNotificationChannelRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def create_project_notification_channel(
    project_id: UUID,
    payload: ProjectNotificationChannelCreate,
    db: DbSession,
) -> ProjectNotificationChannelRead:
    """Create a notification channel (e.g. DingTalk webhook) for a project.

    ``webhook_url`` / ``secret`` 在落库时由 :class:`EncryptedString` 加密；
    响应里统一脱敏为 ``****``。
    """

    await _get_or_404(db, Project, project_id, "Project")
    data = payload.model_dump(exclude={"project_id"})
    channel = ProjectNotificationChannel(project_id=project_id, **data)
    return await _create(
        db,
        channel,
        ProjectNotificationChannelRead,
        "Notification channel already exists",
    )


@router.patch(
    "/projects/{project_id}/notification-channels/{channel_id}",
    response_model=ProjectNotificationChannelRead,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def update_project_notification_channel(
    project_id: UUID,
    channel_id: UUID,
    payload: ProjectNotificationChannelUpdate,
    db: DbSession,
) -> ProjectNotificationChannelRead:
    """Update a project notification channel (partial update).

    前端编辑回显时 ``webhook_url`` / ``secret`` 被脱敏为 ``****``；若用户未
    修改这两个字段，提交的值仍为 ``****``。此处过滤掉掩码值，避免把 ``****``
    当作真实 webhook 落库加密。
    """

    channel = await _get_scoped_notification_channel(db, project_id, channel_id)
    data = payload.model_dump(exclude_unset=True)
    # 过滤掩码值：webhook_url / secret 为 "****" 时视为「未修改」，跳过。
    for masked_field in ("webhook_url", "secret"):
        if data.get(masked_field) == "****":
            data.pop(masked_field, None)
    for field, value in data.items():
        setattr(channel, field, value)
    await _commit_or_400(db, "Notification channel update failed")
    await db.refresh(channel)
    return ProjectNotificationChannelRead.model_validate(channel)


@router.delete(
    "/projects/{project_id}/notification-channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def delete_project_notification_channel(
    project_id: UUID,
    channel_id: UUID,
    db: DbSession,
) -> None:
    """Delete a project notification channel."""

    channel = await _get_scoped_notification_channel(db, project_id, channel_id)
    repo = ProjectNotificationChannelRepository(db)
    await repo.delete(channel, flush=False)
    await _commit_or_400(db, "Notification channel delete failed")


async def _get_scoped_notification_channel(
    db: AsyncSession,
    project_id: UUID,
    channel_id: UUID,
) -> ProjectNotificationChannel:
    """Fetch a notification channel scoped to a project or raise 404.

    复用通用 ``_get_or_404`` 取出渠道后额外校验 ``project_id`` 一致，避免跨项目
    读写（``channel_id`` 全局唯一但更新 / 删除必须限定在路径声明的项目下）。
    """

    channel = await _get_or_404(db, ProjectNotificationChannel, channel_id, "Notification channel")
    if channel.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification channel not found",
        )
    return channel


@router.get(
    "/projects/{project_id}/user-mappings",
    response_model=list[UserMappingResponse],
    dependencies=[Depends(_require_permission("page:user-mappings"))],
)
async def list_project_user_mappings(
    project_id: UUID,
    db: DbSession,
) -> list[UserMappingResponse]:
    """List GitLab↔DingTalk user mappings configured for a project."""

    await _get_or_404(db, Project, project_id, "Project")
    repo = UserMappingRepository(db)
    mappings = await repo.list_by_project(project_id)
    return [UserMappingResponse.model_validate(mapping) for mapping in mappings]


@router.post(
    "/projects/{project_id}/user-mappings",
    response_model=UserMappingResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_permission("page:user-mappings"))],
)
async def create_project_user_mapping(
    project_id: UUID,
    payload: UserMappingCreate,
    db: DbSession,
) -> UserMappingResponse:
    """Create a GitLab↔DingTalk user mapping for a project.

    同一项目下 ``gitlab_username`` 唯一；重复创建会因唯一约束返回 409。
    """

    await _get_or_404(db, Project, project_id, "Project")
    mapping = UserMapping(
        project_id=project_id,
        **payload.model_dump(exclude={"project_id"}),
    )
    # 与 notification-channels 一致：flush=False 让唯一约束冲突在 commit 时
    # 暴露，由 _commit_or_400 统一映射为 409。
    repo = UserMappingRepository(db)
    await repo.add(mapping, flush=False)
    await _commit_or_400(db, "User mapping already exists")
    await db.refresh(mapping)
    return UserMappingResponse.model_validate(mapping)


@router.put(
    "/user-mappings/{mapping_id}",
    response_model=UserMappingResponse,
    dependencies=[Depends(_require_permission("page:user-mappings"))],
)
async def update_user_mapping(
    mapping_id: UUID,
    payload: UserMappingUpdate,
    db: DbSession,
) -> UserMappingResponse:
    """Update a user mapping (partial update semantics; unset fields unchanged)."""

    mapping = await _get_or_404(db, UserMapping, mapping_id, "User mapping")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(mapping, field, value)
    # commit 阶段统一由 _commit_or_400 处理唯一约束冲突（改 gitlab_username 撞车 -> 409）。
    await _commit_or_400(db, "User mapping update failed")
    await db.refresh(mapping)
    return UserMappingResponse.model_validate(mapping)


@router.delete(
    "/user-mappings/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_permission("page:user-mappings"))],
)
async def delete_user_mapping(mapping_id: UUID, db: DbSession) -> None:
    """Delete a user mapping."""

    mapping = await _get_or_404(db, UserMapping, mapping_id, "User mapping")
    repo = UserMappingRepository(db)
    await repo.delete(mapping, flush=False)
    await _commit_or_400(db, "User mapping delete failed")


def _project_select() -> Select[tuple[Project]]:
    """Build project select with nested relationships loaded for response serialization."""

    return select(Project).options(
        selectinload(Project.project_rules),
        selectinload(Project.block_policies),
        selectinload(Project.notification_channels),
    )


async def _load_project_read(db: AsyncSession, project_id: UUID) -> ProjectRead:
    """Load and serialize a project with nested rules and policies."""

    result = await db.execute(_project_select().where(Project.id == project_id))
    project = result.scalars().unique().one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return ProjectRead.model_validate(project)


def _replace_project_rules(project: Project, rules: Sequence[ProjectRuleCreate]) -> None:
    """Replace project-rule associations from request payload."""

    project.project_rules = [
        ProjectRule(**rule.model_dump(exclude={"project_id"}))
        for rule in rules
    ]


async def _default_blocker_project_rules(db: AsyncSession) -> list[ProjectRuleCreate]:
    """默认关联规则：所有 ``enabled && severity_default='BLOCKER'`` 的规则。

    只在 ``ProjectCreate.rules is None`` 时（前端未显式传规则关联）触发，
    保证新项目「安全默认」——一进来就有基础安全规则在跑。

    - ``enabled=True``：跟随 payload 默认；
    - ``severity_override=None``：使用规则自己的 severity_default，不覆盖。
    - 未来 seed 更多 BLOCKER 规则会自动生效。

    只查 ID 减少内存占用；`RuleRepository` 目前没有专用方法就直接构造语句。
    """

    stmt = select(Rule.id).where(
        Rule.enabled.is_(True),
        Rule.severity_default == "BLOCKER",
    )
    result = await db.execute(stmt)
    rule_ids = [row[0] for row in result.all()]
    return [
        ProjectRuleCreate(rule_id=rule_id, enabled=True, severity_override=None)
        for rule_id in rule_ids
    ]


def _replace_block_policies(project: Project, policies: Sequence[ProjectBlockPolicyCreate]) -> None:
    """Replace branch block policies from request payload ordered by priority."""

    project.block_policies = [
        ProjectBlockPolicy(**policy.model_dump(exclude={"project_id"}))
        for policy in sorted(policies, key=lambda item: item.priority)
    ]
