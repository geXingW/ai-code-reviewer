"""登录与当前用户信息路由。

``/auth/login`` 不需要 Bearer token（凭据校验在函数内完成），``/auth/me``
要求合法 JWT。登录签发 JWT，payload 里的 ``sub`` 是用户名，权限随角色实时解析。
"""

from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.admin.deps import (
    ALL_PERMISSIONS_LEGACY,
    _require_admin_auth,
    _sign_token,
)
from core.config import get_settings
from core.db import DbSession
from models.user import User
from repositories import (
    UserRepository,
)
from schemas.user import (
    UserLoginResponse,
)

logger = logging.getLogger(__name__)

login_router = APIRouter(prefix="/api", tags=["admin"])


class LoginRequest(BaseModel):
    """MVP single-account login payload."""

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    """MVP bearer token response.

    ``username`` 是随登录一起返回的当前用户名，前端会存到 sessionStorage 后
    在误报处理弹窗里作为「标记人 / 审核人」的默认值。仅用于前端展示与默认值
    预填，不参与服务端鉴权（鉴权仍走 Authorization 头里的 JWT）。
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    username: str


@login_router.post("/auth/login", response_model=UserLoginResponse)
async def login(payload: LoginRequest, db: DbSession) -> UserLoginResponse:
    """Authenticate a user and return a signed bearer token with permissions."""
    settings = get_settings()

    # Try RBAC first
    if settings.rbac_enabled:
        user_repo = UserRepository(db)
        user = await user_repo.get_by_username(payload.username)

        if user is not None and user.enabled:
            if bcrypt.checkpw(
                payload.password.encode("utf-8"),
                user.password_hash.encode("utf-8"),
            ):
                permissions = _extract_permissions(user)
                project_ids = _extract_project_ids(user)
                expires_in = settings.jwt_expires_in
                expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
                return UserLoginResponse(
                    access_token=_sign_token(user.username, expires_at),
                    expires_in=expires_in,
                    username=user.username,
                    display_name=user.display_name,
                    permissions=permissions,
                    project_ids=project_ids,
                )

    # Fall back to legacy admin login
    expected_username = settings.admin_username
    expected_password = settings.admin_password.get_secret_value()
    if not hmac.compare_digest(payload.username, expected_username) or not hmac.compare_digest(
        payload.password,
        expected_password,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    expires_in = settings.jwt_expires_in
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    return UserLoginResponse(
        access_token=_sign_token(payload.username, expires_at),
        expires_in=expires_in,
        username=payload.username,
        display_name="管理员",
        permissions=ALL_PERMISSIONS_LEGACY,
        project_ids=[],
    )


def _extract_permissions(user: User) -> list[str]:
    """Extract permission strings from a user's role."""
    if user.role is None:
        return []
    return [rp.permission for rp in user.role.permissions]


def _extract_project_ids(user: User) -> list[UUID]:
    """Extract assigned project IDs from a user."""
    return [assign.project_id for assign in user.project_assignments]


@login_router.get("/auth/me", response_model=UserLoginResponse)
async def get_current_user(
    request: Request,
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UserLoginResponse:
    """Return the current authenticated user's info."""
    ctx = await _require_admin_auth(request, db, authorization)
    user = ctx.get("user")
    if user is not None:
        return UserLoginResponse(
            access_token="",  # 不回传 token
            expires_in=0,
            username=user.username,
            display_name=user.display_name,
            permissions=ctx["permissions"],
            project_ids=ctx["project_ids"],
        )
    # Legacy admin fallback
    return UserLoginResponse(
        access_token="",
        expires_in=0,
        username=get_settings().admin_username,
        display_name="管理员",
        permissions=ALL_PERMISSIONS_LEGACY,
        project_ids=[],
    )
