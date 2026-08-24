# PR-3：RBAC 权限拦截生效 + /api/auth/me 端点 + 项目列表按用户过滤

## 绝对约束

- **不要修改测试文件**（`tests/*.py` 一律不动）
- **不要修改现有路由的业务逻辑**（只加 `dependencies`，不改函数体）
- **不要修改 pyproject.toml / ruff.toml 等配置文件**
- **不要修改 alembic 迁移、模型、schema 文件**
- **不要修改 webhook 相关文件**（`backend/api/gitlab_webhook.py`）

## 文件清单

### 修改文件（5 个）

1. `backend/api/admin.py` — 为所有路由添加 `_require_permission` 守卫 + 新增 `/api/auth/me` 端点
2. `backend/api/main.py` — 确认 stats 路由依赖已更新
3. `backend/api/stats.py` — 为 stats 路由添加 `_require_admin_auth` 依赖

---

## 详细任务

### Task 26: admin.py 路由加权限守卫

**文件：`backend/api/admin.py`**

**核心原则：** 每个路由根据其资源类型，添加对应的 `page:*` 权限守卫。`users_router`（RBAC）的路由加 `user:*` / `role:*` 操作权限。

#### 26a. 新增 `/api/auth/me` 端点

在 `login_router` 上新增，用于前端获取当前用户信息：

```python
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
```

#### 26b. 为现有路由添加权限守卫

路由与权限映射表：

| 路由组 | 路由 | 权限 |
|--------|------|------|
| **providers** | GET/POST `/api/providers` | `page:providers` |
| | GET/PATCH/DELETE `/api/providers/{id}` | `page:providers` |
| **rules** | GET/POST `/api/rules` | `page:rules` |
| | GET/PATCH/DELETE `/api/rules/{id}` | `page:rules` |
| **projects** | GET/POST `/api/projects` | `page:projects` |
| | GET/PATCH/DELETE `/api/projects/{id}` | `page:projects` |
| | notification-channels (CRUD) | `page:projects` |
| | negative-prompt (CRUD + generate) | `page:projects` |
| **user-mappings** | GET/POST `/api/projects/{id}/user-mappings` | `page:user-mappings` |
| | PUT/DELETE `/api/projects/{id}/user-mappings/{id}` | `page:user-mappings` |
| **reviews** | GET `/api/reviews/recent` | `page:reviews` |
| | GET/POST `/api/reviews` | `page:reviews` |
| | GET/PATCH/DELETE `/api/reviews/{id}` | `page:reviews` |
| **findings** | GET/POST `/api/findings` | `page:findings` |
| | GET/PATCH/DELETE `/api/findings/{id}` | `page:findings` |
| | POST `/api/findings/{id}/false-positive` | `page:findings` |
| | POST `/api/findings/{id}/resolve` | `page:findings` |
| **falsePositives** | GET `/api/false-positives/pending` | `page:falsePositives` |
| | POST confirm/reject/reset | `page:falsePositives` |
| **negativeExamples** | GET `/api/negative-examples` | `page:negativeExamples` |
| **engines** | GET/POST `/api/engines/configs` | `page:engines` |
| | GET/PATCH/DELETE `/api/engines/configs/{id}` | `page:engines` |
| **global-prompt** | GET/PUT `/api/settings/global-prompt` | `page:global-prompt` |
| **users** (RBAC) | GET `/api/users` | `user:create` 或 `page:users` |
| | POST `/api/users` | `user:create` |
| | GET/PATCH/DELETE `/api/users/{id}` | `user:edit` |
| | PUT `/api/users/{id}/projects` | `project:assign` |
| **roles** (RBAC) | GET `/api/roles` | `role:create` 或 `page:roles` |
| | POST `/api/roles` | `role:create` |
| | GET/PATCH/DELETE `/api/roles/{id}` | `role:edit` |

**实现方式：** 使用 `dependencies` 参数添加权限守卫，而非修改函数签名。

对 `router`（已有 `dependencies=[Depends(_require_admin_auth)]`）上的路由，额外添加 `_require_permission`：

```python
# 示例：list_providers 加 page:providers
@router.get("/providers", response_model=Page, dependencies=[Depends(_require_permission("page:providers"))])
async def list_providers(...):
    ...
```

注意：`router` 已有全局 `dependencies=[Depends(_require_admin_auth)]`，路由级 `dependencies` 会追加，不会覆盖。

#### 26c. RBAC 路由的权限守卫

`users_router` 已有 `dependencies=[Depends(_require_admin_auth)]`，需要逐路由添加：

```python
@users_router.get("/users", dependencies=[Depends(_require_permission("page:users"))])
async def list_users(...): ...

@users_router.post("/users", dependencies=[Depends(_require_permission("user:create"))])
async def create_user(...): ...

@users_router.get("/users/{user_id}", dependencies=[Depends(_require_permission("user:edit"))])
async def get_user(...): ...

@users_router.patch("/users/{user_id}", dependencies=[Depends(_require_permission("user:edit"))])
async def update_user(...): ...

@users_router.delete("/users/{user_id}", dependencies=[Depends(_require_permission("user:delete"))])
async def delete_user(...): ...

@users_router.put("/users/{user_id}/projects", dependencies=[Depends(_require_permission("project:assign"))])
async def assign_user_projects(...): ...

@users_router.get("/roles", dependencies=[Depends(_require_permission("page:roles"))])
async def list_roles(...): ...

@users_router.post("/roles", dependencies=[Depends(_require_permission("role:create"))])
async def create_role(...): ...

@users_router.get("/roles/{role_id}", dependencies=[Depends(_require_permission("role:edit"))])
async def get_role(...): ...

@users_router.patch("/roles/{role_id}", dependencies=[Depends(_require_permission("role:edit"))])
async def update_role(...): ...

@users_router.delete("/roles/{role_id}", dependencies=[Depends(_require_permission("role:delete"))])
async def delete_role(...): ...
```

#### 26d. 项目列表按用户过滤

修改 `list_projects` 函数，根据 `request.state.project_ids` 过滤：

```python
@router.get("/projects", response_model=Page, dependencies=[Depends(_require_permission("page:projects"))])
async def list_projects(
    db: DbSession,
    request: Request,
    limit: ...,
    offset: ...,
    sort: ...,
) -> Page:
    stmt = _project_select()
    project_ids = getattr(request.state, "project_ids", [])
    # 如果用户有指定项目列表（非超级管理员），只返回分配的项目
    if project_ids:
        stmt = stmt.where(Project.id.in_(project_ids))
    return await _paginate(db, stmt, ProjectRead, "projects", sort, limit, offset)
```

超级管理员（`is_legacy_admin=True` 或 `project_ids` 为空列表）不受限制，看全部。

---

### Task 27: 确认 webhook 路由不受影响

**文件：`backend/api/gitlab_webhook.py`**

不修改此文件。确认其使用 `X-Internal-Token` 认证，不经过 JWT 用户认证路径。

---

### Task 28: 确认 RBAC_ENABLED 开关

**文件：`backend/core/config.py`**

`rbac_enabled` 配置项已在 PR-1 中实现（默认 `True`）。`_verify_token_and_load_user` 在 `rbac_enabled=False` 时回退到 legacy admin 模式，所有权限检查自动放行。无需额外修改。

---

### Task 29: 端到端验证

**不创建新的测试文件。** 仅通过以下手动验证确认：

1. 启动后端服务
2. 用 admin 账号登录 → 能访问所有页面
3. 创建一个只有 `page:dashboard` 和 `page:reviews` 权限的角色
4. 创建一个该角色的用户
5. 用新用户登录 → 只有仪表盘和审查记录可见
6. 尝试直接访问 `/api/providers` → 403

验证命令：
```bash
# 登录 admin
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq .access_token

# 用 token 访问 /api/auth/me
curl -s http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer <TOKEN>" | jq .
```

---

## 验证标准

- 所有路由添加 `_require_permission` 后，`python -m pytest backend/tests/ -x -q` 全部通过
- 超级管理员（admin）能访问所有路由
- 新用户按角色权限访问受限路由时返回 403
- `/api/auth/me` 端点返回当前用户信息
- 项目列表按用户分配过滤
- webhook 路由不受影响