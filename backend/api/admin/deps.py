"""管理后台的鉴权与权限守卫（FastAPI ``Depends`` 提供者）。

- :func:`_require_admin_auth`：解析 ``Authorization: Bearer <JWT>``，把
  ``current_user`` / ``permissions`` / ``project_ids`` 挂到 ``request.state``。
- :func:`_require_permission`：路由级权限守卫，前端按同一套 ``page:*`` 权限过滤导航。
- RBAC 关闭或 users 表为空时退回"单管理员"模式（``ALL_PERMISSIONS_LEGACY``）。
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import jwt as pyjwt
from fastapi import Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.db import DbSession
from models.user import User
from repositories import (
    UserRepository,
)

logger = logging.getLogger(__name__)

# Legacy fallback: all permissions for old admin-only mode.
ALL_PERMISSIONS_LEGACY: list[str] = [
    "page:dashboard",
    "page:providers",
    "page:global-prompt",
    "page:rules",
    "page:projects",
    "page:user-mappings",
    "page:reviews",
    "page:findings",
    "page:falsePositives",
    "page:negativeExamples",
    "page:engines",
    "page:users",
    "page:roles",
    "user:create",
    "user:edit",
    "user:delete",
    "role:create",
    "role:edit",
    "role:delete",
    "project:assign",
]


def _unauthorized() -> HTTPException:
    """Build a consistent 401 response for admin bearer authentication failures."""

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid admin token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _require_admin_auth(
    request: Request,
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> dict[str, Any]:
    """Validate the admin bearer token and load the user context.

    Injects ``request.state.current_user``, ``request.state.permissions``,
    and ``request.state.project_ids`` for downstream use.
    """
    if authorization is None:
        raise _unauthorized()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    ctx = await _verify_token_and_load_user(token.strip(), db)
    request.state.current_user = ctx["user"]
    request.state.permissions = ctx["permissions"]
    request.state.project_ids = ctx["project_ids"]
    request.state.is_legacy_admin = ctx["is_legacy_admin"]
    return ctx


def _require_permission(permission: str) -> Callable[[Request], None]:
    """FastAPI dependency: require the current user to have a specific permission.

    Usage:
        @router.get("/users", dependencies=[Depends(_require_permission("user:create"))])
    """
    def check_permission(request: Request) -> None:
        if not hasattr(request.state, "permissions"):
            raise _unauthorized()
        if permission not in request.state.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission}",
            )
    return check_permission


async def _verify_token_and_load_user(
    token: str,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    """Verify a JWT and load the user with role, permissions, and project assignments.

    Returns a dict with "user" (User object) and "permissions" (list[str]) and
    "project_ids" (list[UUID]).
    Falls back to legacy admin-only mode if users table is empty.
    """
    settings = get_settings()
    try:
        payload = pyjwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except pyjwt.PyJWTError as exc:
        raise _unauthorized() from exc

    username = str(payload.get("sub", ""))

    # Legacy fallback: if RBAC is disabled or users table is empty, use old admin check
    if not settings.rbac_enabled:
        if not hmac.compare_digest(username, settings.admin_username):
            raise _unauthorized()
        return {
            "user": None,
            "permissions": ALL_PERMISSIONS_LEGACY,
            "project_ids": [],
            "is_legacy_admin": True,
        }

    if db is None:
        # No DB session available — fall back to legacy
        if not hmac.compare_digest(username, settings.admin_username):
            raise _unauthorized()
        return {
            "user": None,
            "permissions": ALL_PERMISSIONS_LEGACY,
            "project_ids": [],
            "is_legacy_admin": True,
        }

    user_repo = UserRepository(db)
    user = await user_repo.get_by_username(username)

    if user is None:
        # Check if users table is empty (seed not yet run) — fall back to legacy
        from sqlalchemy import func as _sa_func
        from sqlalchemy import select as _sa_select

        count_result = await db.execute(_sa_select(_sa_func.count()).select_from(User))
        if count_result.scalar_one() == 0:
            if not hmac.compare_digest(username, settings.admin_username):
                raise _unauthorized()
            return {
                "user": None,
                "permissions": ALL_PERMISSIONS_LEGACY,
                "project_ids": [],
                "is_legacy_admin": True,
            }
        raise _unauthorized()

    if not user.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled")

    # Extract permissions from role
    permissions: list[str] = []
    if user.role is not None:
        for rp in user.role.permissions:
            permissions.append(rp.permission)

    # Extract project IDs
    project_ids: list[UUID] = [
        assign.project_id for assign in user.project_assignments
    ]

    return {
        "user": user,
        "permissions": permissions,
        "project_ids": project_ids,
        "is_legacy_admin": False,
    }


def _sign_token(username: str, expires_at: datetime) -> str:
    """Create a standard JWT signed with the configured secret and algorithm."""

    settings = get_settings()
    payload = {
        "sub": username,
        "exp": int(expires_at.timestamp()),
        "iat": int(datetime.now(UTC).timestamp()),
    }
    return pyjwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
