"""RBAC 路由：用户、项目授权、角色与权限矩阵。

角色权限是 ``page:*``（页面可见性）+ 动作权限（``user:*`` / ``role:*`` /
``project:assign``）；用户通过 ``user_project_assignments`` 限定可见项目。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from api.admin.common import (
    Page,
    _paginate,
)
from api.admin.deps import (
    _require_admin_auth,
    _require_permission,
)
from core.db import DbSession
from models.role import Role
from models.role_permission import RolePermission
from models.user import User
from models.user_project_assignment import UserProjectAssignment
from repositories import (
    RoleRepository,
    UserRepository,
)
from schemas.role import RoleCreate, RoleRead, RoleUpdate
from schemas.user import (
    UserCreate,
    UserProjectAssignRequest,
    UserRead,
    UserUpdate,
)

logger = logging.getLogger(__name__)


def _enrich_user_read(user: User) -> dict[str, Any]:
    """Build enrichment dict for UserRead from ORM User object."""
    permissions: list[str] = []
    role_name: str | None = None
    if user.role is not None:
        role_name = user.role.name
        permissions = [rp.permission for rp in user.role.permissions]
    project_ids = [a.project_id for a in user.project_assignments]
    return {
        "role_name": role_name,
        "permissions": permissions,
        "project_ids": project_ids,
    }


def _user_to_read(user: User) -> UserRead:
    """Serialize an ORM User into a fully-enriched UserRead."""
    return UserRead.model_validate(user).model_copy(update=_enrich_user_read(user))


users_router = APIRouter(
    prefix="/api",
    tags=["admin"],
    dependencies=[Depends(_require_admin_auth)],
)


@users_router.get(
    "/users",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:users"))],
)
async def list_users(
    db: DbSession,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List all users with pagination."""
    del request
    stmt = select(User).options(
        selectinload(User.role).selectinload(Role.permissions),
        selectinload(User.project_assignments),
    )
    return await _paginate(db, stmt, UserRead, "users", sort, limit, offset, enrich=_user_to_read)


@users_router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_permission("user:create"))],
)
async def create_user(payload: UserCreate, db: DbSession) -> UserRead:
    """Create a new user."""
    user_repo = UserRepository(db)

    # Check uniqueness
    existing = await user_repo.get_by_username(payload.username)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    user = User(
        username=payload.username,
        password_hash=bcrypt.hashpw(
            payload.password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8"),
        display_name=payload.display_name,
        role_id=payload.role_id,
        enabled=payload.enabled,
    )
    await user_repo.add(user)
    await db.commit()
    await db.refresh(user, attribute_names=["role", "project_assignments"])

    return _user_to_read(user)


@users_router.get(
    "/users/{user_id}",
    response_model=UserRead,
    dependencies=[Depends(_require_permission("user:edit"))],
)
async def get_user(user_id: UUID, db: DbSession) -> UserRead:
    """Get a single user by ID."""
    user_repo = UserRepository(db)
    user = await user_repo.get_with_relations(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _user_to_read(user)


@users_router.patch(
    "/users/{user_id}",
    response_model=UserRead,
    dependencies=[Depends(_require_permission("user:edit"))],
)
async def update_user(user_id: UUID, payload: UserUpdate, db: DbSession) -> UserRead:
    """Update an existing user."""
    user_repo = UserRepository(db)
    user = await user_repo.get_with_relations(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "password" in update_data and update_data["password"]:
        update_data["password_hash"] = bcrypt.hashpw(
            update_data.pop("password").encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

    if "username" in update_data and update_data["username"] != user.username:
        existing = await user_repo.get_by_username(update_data["username"])
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username already exists",
            )

    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user, attribute_names=["role", "project_assignments"])

    return _user_to_read(user)


@users_router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_permission("user:delete"))],
)
async def delete_user(user_id: UUID, db: DbSession) -> None:
    """Soft-delete a user (set enabled=False)."""
    user_repo = UserRepository(db)
    user = await user_repo.get(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.enabled = False
    await db.commit()


@users_router.put(
    "/users/{user_id}/projects",
    response_model=UserRead,
    dependencies=[Depends(_require_permission("project:assign"))],
)
async def assign_user_projects(
    user_id: UUID,
    payload: UserProjectAssignRequest,
    db: DbSession,
) -> UserRead:
    """Replace the user's project assignments."""
    user_repo = UserRepository(db)
    user = await user_repo.get_with_relations(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Clear existing assignments; flush first so the DELETEs execute before
    # the new INSERTs (same-flush order is INSERT before DELETE, which would
    # violate the uniq_user_project constraint when a project is re-assigned)
    for assignment in list(user.project_assignments):
        await db.delete(assignment)
    await db.flush()

    # Create new assignments
    for project_id in payload.project_ids:
        db.add(UserProjectAssignment(user_id=user_id, project_id=project_id))

    await db.commit()
    await db.refresh(user, attribute_names=["role", "project_assignments"])

    return _user_to_read(user)


@users_router.get(
    "/roles",
    response_model=Page,
    dependencies=[Depends(_require_permission("page:roles"))],
)
async def list_roles(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page:
    """List all roles."""
    role_repo = RoleRepository(db)
    stmt = select(Role).options(selectinload(Role.permissions)).order_by(Role.created_at.asc())
    result = await db.execute(stmt.offset(offset).limit(limit))
    roles = list(result.scalars().all())

    items = []
    for role in roles:
        items.append(
            RoleRead(
                id=role.id,
                name=role.name,
                description=role.description,
                is_system=role.is_system,
                permissions=[rp.permission for rp in role.permissions],
                user_count=await role_repo.count_users(role.id),
                created_at=role.created_at,
                updated_at=role.updated_at,
            )
        )

    count_stmt = select(func.count()).select_from(Role)
    total = await db.execute(count_stmt)
    return Page(items=items, total=total.scalar_one(), limit=limit, offset=offset)


@users_router.post(
    "/roles",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_permission("role:create"))],
)
async def create_role(payload: RoleCreate, db: DbSession) -> RoleRead:
    """Create a new role with permissions."""
    role_repo = RoleRepository(db)

    existing = await role_repo.get_by_name(payload.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Role name already exists",
        )

    role = Role(name=payload.name, description=payload.description)
    await role_repo.add(role)

    for perm in payload.permissions:
        db.add(RolePermission(role_id=role.id, permission=perm))

    await db.commit()
    await db.refresh(role, attribute_names=["permissions"])

    return RoleRead(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=[rp.permission for rp in role.permissions],
        user_count=0,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


@users_router.get(
    "/roles/{role_id}",
    response_model=RoleRead,
    dependencies=[Depends(_require_permission("role:edit"))],
)
async def get_role(role_id: UUID, db: DbSession) -> RoleRead:
    """Get a single role by ID."""
    role_repo = RoleRepository(db)
    role = await role_repo.get_with_permissions(role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    return RoleRead(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=[rp.permission for rp in role.permissions],
        user_count=await role_repo.count_users(role.id),
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


@users_router.patch(
    "/roles/{role_id}",
    response_model=RoleRead,
    dependencies=[Depends(_require_permission("role:edit"))],
)
async def update_role(role_id: UUID, payload: RoleUpdate, db: DbSession) -> RoleRead:
    """Update a role and its permissions."""
    role_repo = RoleRepository(db)
    role = await role_repo.get_with_permissions(role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "name" in update_data:
        existing = await role_repo.get_by_name(update_data["name"])
        if existing is not None and existing.id != role_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Role name already exists",
            )
        role.name = update_data["name"]

    if "description" in update_data:
        role.description = update_data["description"]

    if "permissions" in update_data:
        # Replace all permissions; flush first so DELETEs execute before the
        # new INSERTs (same-flush order is INSERT before DELETE, which would
        # violate the uniq_role_permission constraint for retained permissions)
        for rp in list(role.permissions):
            await db.delete(rp)
        await db.flush()
        for perm in update_data["permissions"]:
            db.add(RolePermission(role_id=role.id, permission=perm))

    await db.commit()
    await db.refresh(role, attribute_names=["permissions"])

    return RoleRead(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=[rp.permission for rp in role.permissions],
        user_count=await role_repo.count_users(role.id),
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


@users_router.delete(
    "/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_permission("role:delete"))],
)
async def delete_role(role_id: UUID, db: DbSession) -> None:
    """Delete a role (system roles cannot be deleted)."""
    role_repo = RoleRepository(db)
    role = await role_repo.get(role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete a system role",
        )
    await role_repo.delete(role)
    await db.commit()
