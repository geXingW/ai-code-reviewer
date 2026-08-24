# PR-1：RBAC 后端数据模型 + 迁移 + 基础 CRUD + JWT 改造

## 绝对约束

- **不要修改任何测试文件**（`tests/test_*.py` 一律不动）
- **不要修改现有 API 路由的业务逻辑**（只改认证层，不改路由处理函数体）
- **不要删除或重命名现有的任何类、函数、文件**
- **不要修改 pyproject.toml / ruff.toml 等配置文件**
- **不要修改 alembic/env.py 或任何已有迁移文件**

## 文件清单

你需要创建/修改以下文件：

### 新建文件（9 个）

1. `backend/models/user.py` — User 模型
2. `backend/models/role.py` — Role 模型
3. `backend/models/role_permission.py` — RolePermission 模型
4. `backend/models/user_project_assignment.py` — UserProjectAssignment 模型
5. `backend/schemas/user.py` — User 的 Pydantic schema
6. `backend/schemas/role.py` — Role 的 Pydantic schema
7. `backend/repositories/user_repository.py` — UserRepository
8. `backend/repositories/role_repository.py` — RoleRepository
9. `backend/core/seed.py` — 超级管理员种子数据

### 修改文件（6 个）

10. `backend/models/__init__.py` — 导出新模型
11. `backend/schemas/__init__.py` — 导出新 schema
12. `backend/repositories/__init__.py` — 导出新 repository
13. `backend/api/admin.py` — 新增用户/角色 CRUD API + 改造认证
14. `backend/core/config.py` — 新增 `rbac_enabled` 配置项
15. `backend/main.py` — 注册新路由

### 新建迁移文件（1 个）

16. `backend/alembic/versions/0013_rbac_users_roles.py` — 创建 4 张表

---

## 详细任务

### Task 1: 创建 User 模型

文件：`backend/models/user.py`

```python
"""SQLAlchemy model for RBAC users."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from models.role import Role
    from models.user_project_assignment import UserProjectAssignment


class User(Base, TimestampMixin):
    """Internal user account for the RBAC management system."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(
        String(256), nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    role_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("roles.id", ondelete="SET NULL"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    role: Mapped["Role | None"] = relationship(
        back_populates="users", lazy="selectin"
    )
    project_assignments: Mapped[list["UserProjectAssignment"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
```

### Task 2: 创建 Role 模型

文件：`backend/models/role.py`

```python
"""SQLAlchemy model for RBAC roles."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from models.role_permission import RolePermission
    from models.user import User


class Role(Base, TimestampMixin):
    """RBAC role with a set of permissions."""

    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    users: Mapped[list["User"]] = relationship(
        back_populates="role", lazy="selectin"
    )
    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
```

### Task 3: 创建 RolePermission 模型

文件：`backend/models/role_permission.py`

```python
"""SQLAlchemy model for role permissions."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base

if TYPE_CHECKING:
    from models.role import Role


class RolePermission(Base):
    """A single permission string assigned to a role."""

    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission", name="uniq_role_permission"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    role_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission: Mapped[str] = mapped_column(
        String(128), nullable=False
    )

    role: Mapped["Role"] = relationship(
        back_populates="permissions"
    )
```

### Task 4: 创建 UserProjectAssignment 模型

文件：`backend/models/user_project_assignment.py`

```python
"""SQLAlchemy model for user-project assignments."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base

if TYPE_CHECKING:
    from models.project import Project
    from models.user import User


class UserProjectAssignment(Base):
    """Maps a user to a project they have access to."""

    __tablename__ = "user_project_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uniq_user_project"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    user: Mapped["User"] = relationship(
        back_populates="project_assignments"
    )
    project: Mapped["Project"] = relationship(
        lazy="selectin"
    )
```

### Task 5: 更新 models/__init__.py

在现有导入后添加：

```python
from models.role import Role
from models.role_permission import RolePermission
from models.user import User
from models.user_project_assignment import UserProjectAssignment
```

在 `__all__` 列表末尾添加 `"Role"`, `"RolePermission"`, `"User"`, `"UserProjectAssignment"`。

### Task 6: 创建 Alembic 迁移

文件：`backend/alembic/versions/0013_rbac_users_roles.py`

先查看最新迁移的 `down_revision`：

```bash
cd backend && ls alembic/versions/0012_*.py
```

确认 revision 为 `0012` 的 ID。然后创建迁移，包含 4 张表：

- `users` 表：id, username (unique index), password_hash, display_name, role_id (FK→roles), enabled, created_at, updated_at
- `roles` 表：id, name (unique), description, is_system, created_at, updated_at
- `role_permissions` 表：id, role_id (FK→roles CASCADE), permission, unique(role_id, permission)
- `user_project_assignments` 表：id, user_id (FK→users CASCADE), project_id (FK→projects CASCADE), unique(user_id, project_id)

### Task 7: 创建 User Schema

文件：`backend/schemas/user.py`

```python
"""Pydantic schemas for RBAC users."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    """Payload for creating a new user."""
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    role_id: UUID | None = None
    enabled: bool = True


class UserUpdate(BaseModel):
    """Payload for updating an existing user (all fields optional)."""
    username: str | None = Field(default=None, min_length=1, max_length=64)
    password: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    role_id: UUID | None = None
    enabled: bool | None = None


class UserRead(BaseModel):
    """Response for a user record."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str | None
    role_id: UUID | None
    role_name: str | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    # 由 enrich 填充
    project_ids: list[UUID] = []
    permissions: list[str] = []


class UserLoginResponse(BaseModel):
    """Response for login (reuses existing LoginResponse shape)."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str
    display_name: str | None = None
    permissions: list[str] = []
    project_ids: list[UUID] = []


class UserProjectAssignRequest(BaseModel):
    """Payload for assigning projects to a user."""
    project_ids: list[UUID] = Field(default_factory=list)
```

### Task 8: 创建 Role Schema

文件：`backend/schemas/role.py`

```python
"""Pydantic schemas for RBAC roles."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RoleCreate(BaseModel):
    """Payload for creating a new role."""
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=256)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """Payload for updating an existing role."""
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=256)
    permissions: list[str] | None = None


class RoleRead(BaseModel):
    """Response for a role record."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    is_system: bool
    permissions: list[str] = []
    user_count: int = 0
    created_at: datetime
    updated_at: datetime
```

### Task 9: 创建 UserRepository

文件：`backend/repositories/user_repository.py`

```python
"""Repository for User model."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from models.user import User
from models.user_project_assignment import UserProjectAssignment
from repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository for User CRUD and queries."""

    model = User

    async def get_by_username(self, username: str) -> User | None:
        """Find a user by username (case-sensitive)."""
        stmt = (
            select(User)
            .options(
                selectinload(User.role).selectinload(
                    __import__("models.role", fromlist=["Role"]).Role.permissions
                ),
                selectinload(User.project_assignments),
            )
            .where(User.username == username)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_with_relations(self, user_id: UUID) -> User | None:
        """Get a user with role, permissions, and project assignments eager-loaded."""
        stmt = (
            select(User)
            .options(
                selectinload(User.role).selectinload(
                    __import__("models.role", fromlist=["Role"]).Role.permissions
                ),
                selectinload(User.project_assignments),
            )
            .where(User.id == user_id)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()
```

**注意：** 上面 `selectinload` 的跨模块引用写法可能有问题。改为更干净的写法——在文件顶部导入 `Role` 和 `RolePermission`，直接用 `selectinload(User.role).selectinload(Role.permissions)`。

### Task 10: 创建 RoleRepository

文件：`backend/repositories/role_repository.py`

```python
"""Repository for Role model."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from models.role import Role
from models.role_permission import RolePermission
from models.user import User
from repositories.base import BaseRepository


class RoleRepository(BaseRepository[Role]):
    """Repository for Role CRUD and queries."""

    model = Role

    async def get_by_name(self, name: str) -> Role | None:
        """Find a role by name."""
        stmt = (
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.name == name)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_with_permissions(self, role_id: UUID) -> Role | None:
        """Get a role with permissions eager-loaded."""
        stmt = (
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.id == role_id)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def count_users(self, role_id: UUID) -> int:
        """Count users assigned to this role."""
        stmt = select(func.count()).select_from(User).where(User.role_id == role_id)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())
```

### Task 11: 更新 repositories/__init__.py

添加导入和导出：

```python
from repositories.role_repository import RoleRepository
from repositories.user_repository import UserRepository
```

在 `__all__` 中添加 `"RoleRepository"`, `"UserRepository"`。

### Task 12: 更新 schemas/__init__.py

添加导入和导出：

```python
from schemas.role import RoleCreate, RoleRead, RoleUpdate
from schemas.user import UserCreate, UserProjectAssignRequest, UserRead, UserUpdate
```

在 `__all__` 中添加所有新 schema 类名。

### Task 13: 创建种子数据模块

文件：`backend/core/seed.py`

```python
"""Seed super admin on first startup."""

from __future__ import annotations

import logging

from sqlalchemy import select

from core.config import get_settings
from core.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

# 所有权限标识
ALL_PERMISSIONS = [
    # 菜单权限
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
    # 操作权限
    "user:create",
    "user:edit",
    "user:delete",
    "role:create",
    "role:edit",
    "role:delete",
    "project:assign",
]


async def seed_super_admin() -> None:
    """Ensure the super_admin role and admin user exist on first startup."""
    from models.role import Role
    from models.role_permission import RolePermission
    from models.user import User
    import bcrypt

    settings = get_settings()

    async with AsyncSessionLocal() as session:
        # Check if there are any users already
        result = await session.execute(select(User).limit(1))
        if result.scalars().first() is not None:
            logger.info("Users already exist, skipping seed")
            return

        # Create super_admin role
        role = Role(
            name="super_admin",
            description="超级管理员，拥有所有权限",
            is_system=True,
        )
        session.add(role)
        await session.flush()

        # Grant all permissions
        for perm in ALL_PERMISSIONS:
            session.add(RolePermission(role_id=role.id, permission=perm))

        # Create admin user
        admin_user = User(
            username=settings.admin_username,
            password_hash=bcrypt.hashpw(
                settings.admin_password.get_secret_value().encode("utf-8"),
                bcrypt.gensalt(),
            ).decode("utf-8"),
            display_name="管理员",
            role_id=role.id,
            enabled=True,
        )
        session.add(admin_user)

        await session.commit()
        logger.info(
            "Seeded super_admin role and admin user (username=%s)",
            settings.admin_username,
        )
```

**注意：** `bcrypt` 需要添加到 `requirements.txt`。检查项目是否已有 `bcrypt` 或 `passlib` 依赖。

### Task 14: 改造 admin.py 认证逻辑

这是最关键的改动。需要修改 `backend/api/admin.py` 的以下部分：

#### 14a: 添加导入

在文件顶部添加：

```python
import bcrypt

from models.role import Role
from models.role_permission import RolePermission
from models.user import User
from models.user_project_assignment import UserProjectAssignment
from repositories.user_repository import UserRepository
from repositories.role_repository import RoleRepository
from schemas.user import (
    UserCreate,
    UserRead,
    UserUpdate,
    UserProjectAssignRequest,
    UserLoginResponse,
)
from schemas.role import RoleCreate, RoleRead, RoleUpdate
```

#### 14b: 改造 `_verify_token` → `_verify_token_and_load_user`

将现有的 `_verify_token` 函数（行 125-142）替换为：

```python
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
        from sqlalchemy import func, select as sa_select
        count_result = await db.execute(sa_select(func.count()).select_from(User))
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
```

在 import 区域后添加：

```python
# Legacy fallback: all permissions for old admin-only mode
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
```

#### 14c: 改造 `_require_admin_auth`

将现有的 `_require_admin_auth`（行 112-122）替换为异步版本，注入 `request.state`：

```python
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
```

注意：需要添加 `from fastapi import Request` 和 `from core.db import DbSession`（DbSession 已经在现有导入中）。

#### 14d: 改造 `login` 端点

修改现有的 `POST /auth/login` 函数（行 194-213）：

```python
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
```

添加辅助函数：

```python
def _extract_permissions(user: User) -> list[str]:
    """Extract permission strings from a user's role."""
    if user.role is None:
        return []
    return [rp.permission for rp in user.role.permissions]


def _extract_project_ids(user: User) -> list[UUID]:
    """Extract assigned project IDs from a user."""
    return [assign.project_id for assign in user.project_assignments]
```

#### 14e: 新增 `_require_permission` 依赖

```python
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
```

#### 14f: 新增用户管理路由

在文件末尾（`_sign_token` 函数之后）添加：

```python
# ────────────────────────────── RBAC Router ──────────────────────────────

users_router = APIRouter(
    prefix="/api",
    tags=["admin"],
    dependencies=[Depends(_require_admin_auth)],
)

# ── 用户管理 ──

@users_router.get("/users", response_model=Page)
async def list_users(
    db: DbSession,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List all users with pagination."""
    user_repo = UserRepository(db)
    stmt = select(User).options(
        selectinload(User.role).selectinload(Role.permissions),
        selectinload(User.project_assignments),
    )
    page = await _paginate(db, stmt, UserRead, "users", sort, limit, offset)
    # Enrich each user with role_name, permissions, and project_ids
    for item in page.items:
        if isinstance(item, UserRead):
            _enrich_user_read(item)
    return page


@users_router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
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

    read = UserRead.model_validate(user)
    _enrich_user_read(read)
    return read


@users_router.get("/users/{user_id}", response_model=UserRead)
async def get_user(user_id: UUID, db: DbSession) -> UserRead:
    """Get a single user by ID."""
    user_repo = UserRepository(db)
    user = await user_repo.get_with_relations(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    read = UserRead.model_validate(user)
    _enrich_user_read(read)
    return read


@users_router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(user_id: UUID, payload: UserUpdate, db: DbSession) -> UserRead:
    """Update an existing user."""
    user_repo = UserRepository(db)
    user = await user_repo.get_with_relations(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "password" in update_data:
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

    read = UserRead.model_validate(user)
    _enrich_user_read(read)
    return read


@users_router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID, db: DbSession) -> None:
    """Soft-delete a user (set enabled=False)."""
    user_repo = UserRepository(db)
    user = await user_repo.get(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.enabled = False
    await db.commit()


@users_router.put("/users/{user_id}/projects", response_model=UserRead)
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

    # Clear existing assignments
    for assignment in list(user.project_assignments):
        await db.delete(assignment)

    # Create new assignments
    for project_id in payload.project_ids:
        db.add(UserProjectAssignment(user_id=user_id, project_id=project_id))

    await db.commit()
    await db.refresh(user, attribute_names=["role", "project_assignments"])

    read = UserRead.model_validate(user)
    _enrich_user_read(read)
    return read


# ── 角色管理 ──

@users_router.get("/roles", response_model=Page)
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
        read = RoleRead(
            id=role.id,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            permissions=[rp.permission for rp in role.permissions],
            user_count=await role_repo.count_users(role.id),
            created_at=role.created_at,
            updated_at=role.updated_at,
        )
        items.append(read)

    count_stmt = select(func.count()).select_from(Role)
    total = await db.execute(count_stmt)
    return Page(items=items, total=total.scalar_one(), limit=limit, offset=offset)


@users_router.post("/roles", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
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

    read = RoleRead(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=[rp.permission for rp in role.permissions],
        user_count=0,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )
    return read


@users_router.get("/roles/{role_id}", response_model=RoleRead)
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


@users_router.patch("/roles/{role_id}", response_model=RoleRead)
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
        # Replace all permissions
        for rp in list(role.permissions):
            await db.delete(rp)
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


@users_router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
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


def _enrich_user_read(read: UserRead) -> None:
    """Enrich a UserRead with role_name, permissions, and project_ids."""
    import contextlib
    # Try to get role_name from the role relationship
    try:
        user = read
        if hasattr(user, "role") and user.role is not None:
            user.role_name = user.role.name
            user.permissions = [rp.permission for rp in user.role.permissions]
    except Exception:
        pass
    try:
        if hasattr(user, "project_assignments"):
            user.project_ids = [a.project_id for a in user.project_assignments]
    except Exception:
        pass
```

**注意：** `_enrich_user_read` 的实现可能有 bug——它操作的是 Pydantic model 不是 ORM 对象。需要改为在构造 `UserRead` 之前从 ORM 对象提取数据。实际上最佳做法是重写 `_enrich_user_read` 为接收 ORM `User` 对象并返回正确的字典：

```python
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
```

然后在构造 `UserRead` 时使用 `UserRead.model_validate(user).model_copy(update=_enrich_user_read(user))`。

### Task 15: 更新 config.py

在 `backend/core/config.py` 的 `Settings` 类中添加：

```python
    rbac_enabled: Annotated[
        bool,
        Field(description="Enable RBAC multi-user mode. Set to False to fall back to legacy single-admin mode."),
    ] = True
```

### Task 16: 更新 main.py

在 `backend/main.py` 中：

1. 导入 `seed_super_admin`：
```python
from core.seed import seed_super_admin
```

2. 在 `lifespan` 的 startup 部分（`load_builtin_engines()` 之后，`yield` 之前）添加：
```python
    await seed_super_admin()
```

3. 注册 `users_router`（在 `admin_router` 之后）：
```python
from api.admin import users_router as rbac_users_router
# ...
app.include_router(rbac_users_router)
```

### Task 17: 检查并添加 bcrypt 依赖

检查 `backend/requirements.txt` 是否已有 `bcrypt`。如果没有，添加：

```
bcrypt>=4.0.0
```

---

## 验证步骤

完成后运行：

```bash
cd backend

# 1. 语法检查
ruff check .

# 2. 类型检查
mypy app

# 3. 生成迁移 SQL（验证迁移可执行）
alembic upgrade head --sql 2>&1 | head -30

# 4. 确认所有导入正确
python -c "from models import User, Role, RolePermission, UserProjectAssignment; print('models OK')"
python -c "from schemas import UserCreate, UserRead, RoleCreate, RoleRead; print('schemas OK')"
python -c "from repositories import UserRepository, RoleRepository; print('repos OK')"

# 5. 运行现有测试（确保不破坏现有功能）
pytest tests/ -x -q
```

---

## 注意事项

1. **不要触碰 `_sign_token` 函数**——它保持原样，JWT 格式不变
2. **`_paginate` 函数**是 admin.py 中已有的通用分页函数，不要修改它
3. **`UserRead` 不直接用 `model_validate`**——因为 `role_name`、`permissions`、`project_ids` 不在 ORM 列中，需要 enrich
4. **`selectinload` 跨模块引用**——`User.role` 已经 lazy=selectin，但 `Role.permissions` 需要显式 selectinload
5. **`RoleRepository.count_users` 的导入**——需要在函数内懒加载 `User` 类，避免循环导入
6. **`request.state` 访问**——`_require_permission` 依赖需要 `request: Request` 参数