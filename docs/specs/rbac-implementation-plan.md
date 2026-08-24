# RBAC 用户与权限管理 实现计划

> **For Hermes:** 使用 Claude Code 实现此计划，分多个 PR 推进。

**目标：** 在 ai-code-reviewer 中实现完整的 RBAC 用户与权限管理——用户 CRUD、自定义角色（可配置菜单权限）、项目级权限分配、前端菜单/项目按权限过滤。

**架构：** 4 张新表（users / roles / role_permissions / user_project_assignments），JWT 从单一 admin 升级为多用户，改造现有 `_require_admin_auth` → `_require_auth` + `_require_permission`。

**技术栈：** Python 3.11+ / SQLAlchemy 2.0 / FastAPI / Alembic / React 18 / TypeScript / Vite

---

## 数据模型

### 表关系

```
users ──N:1── roles ──1:N── role_permissions
  │
  └── N:M ── user_project_assignments ── M:N ── projects
```

### users 表

| 列 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| username | String(64) UNIQUE NOT NULL | 登录名 |
| password_hash | String(256) NOT NULL | bcrypt hash |
| display_name | String(128) | 显示名 |
| role_id | UUID FK → roles.id | 所属角色 |
| enabled | Boolean default=True | 是否启用 |
| created_at / updated_at | TimestampMixin | |

### roles 表

| 列 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| name | String(64) UNIQUE NOT NULL | 角色名 |
| description | String(256) | 描述 |
| is_system | Boolean default=False | 系统内置角色不可删除 |
| created_at / updated_at | TimestampMixin | |

### role_permissions 表

| 列 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| role_id | UUID FK → roles.id | |
| permission | String(128) NOT NULL | 权限标识（如 `page:dashboard`） |
| UNIQUE(role_id, permission) | | |

### user_project_assignments 表

| 列 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users.id | |
| project_id | UUID FK → projects.id | |
| UNIQUE(user_id, project_id) | | |

---

## 权限标识规范

所有权限统一用 `resource:action` 格式：

**菜单权限（page 级）：**
```
page:dashboard
page:providers
page:global-prompt
page:rules
page:projects
page:user-mappings
page:reviews
page:findings
page:falsePositives
page:negativeExamples
page:engines
page:users          ← 新增：用户管理页
page:roles          ← 新增：角色管理页
```

**操作权限（action 级）：**
```
user:create
user:edit
user:delete
role:create
role:edit
role:delete
project:assign      ← 分配项目给用户
```

---

## 向后兼容策略

**超级管理员：** 系统中存在一个特殊的 "super_admin" 角色，其 `is_system=True`，拥有所有权限。现有 `admin` 用户（ENV `ADMIN_USERNAME` / `ADMIN_PASSWORD`）自动升级为超级管理员——首次启动时检测 users 表是否为空，为空则自动创建 admin 用户 + super_admin 角色。

**JWT 改造：** `_verify_token` 不再只验证 `sub == admin_username`，改为从 users 表加载用户信息（含角色、权限列表），注入到 `request.state.current_user`。

---

## 任务分解

### PR-1：后端数据模型 + 迁移 + 基础 CRUD

**范围：** 新建 4 张表 + Alembic 迁移 + 用户/角色管理 API + 超级管理员种子数据

**不包含：** 权限校验拦截、前端改造

#### Task 1：创建 User 模型
- 文件：`backend/models/user.py`
- 包含 `User` 类，字段如上表

#### Task 2：创建 Role 模型
- 文件：`backend/models/role.py`
- 包含 `Role` 类，字段如上表

#### Task 3：创建 RolePermission 模型
- 文件：`backend/models/role_permission.py`

#### Task 4：创建 UserProjectAssignment 模型
- 文件：`backend/models/user_project_assignment.py`

#### Task 5：更新 models/__init__.py 导出

#### Task 6：Alembic 迁移 0013
- 创建 4 张表 + 外键约束

#### Task 7：创建 User Schema
- 文件：`backend/schemas/user.py`
- `UserCreate` / `UserUpdate` / `UserRead` / `UserLoginResponse`

#### Task 8：创建 Role Schema
- 文件：`backend/schemas/role.py`
- `RoleCreate` / `RoleUpdate` / `RoleRead`

#### Task 9：创建 User Repository
- 文件：`backend/repositories/user_repository.py`
- `get_by_username` / `list_all` / 分页

#### Task 10：创建 Role Repository
- 文件：`backend/repositories/role_repository.py`

#### Task 11：用户管理 API
- 文件：`backend/api/admin.py` — 新增 `users_router`
- `GET /api/users` — 列表（分页）
- `POST /api/users` — 创建
- `GET /api/users/{id}` — 详情
- `PATCH /api/users/{id}` — 编辑（含分配项目和角色）
- `DELETE /api/users/{id}` — 删除（软删除，设 enabled=false）
- `PUT /api/users/{id}/projects` — 分配项目

#### Task 12：角色管理 API
- `GET /api/roles` — 列表
- `POST /api/roles` — 创建（含权限列表）
- `GET /api/roles/{id}` — 详情
- `PATCH /api/roles/{id}` — 编辑（含权限更新）
- `DELETE /api/roles/{id}` — 删除（is_system 角色不可删）

#### Task 13：超级管理员种子数据
- 文件：`backend/core/seed.py` — `seed_super_admin()`
- 应用启动时调用（在 `create_app` 中）
- 检测 users 表是否为空 → 创建 `super_admin` 角色 + 所有权限 + `admin` 用户

#### Task 14：改造 JWT 认证
- `_verify_token` → `_verify_token_and_load_user`
- 从 JWT sub 提取 username → 查 users 表 → 加载角色 + 权限 + 项目分配
- 注入 `request.state.current_user`（User 对象，含 `role.permissions` 和 `assigned_project_ids`）
- 向后兼容：如果 users 表为空（种子数据未跑），回退到旧的 admin_username 单账号模式

#### Task 15：改造登录 API
- `POST /api/auth/login` 不再只验 admin_username/admin_password
- 改为：查 users 表 → 验 bcrypt 密码 → 签发 JWT（sub=username, role=role_name）

#### Task 16：新增权限校验依赖
- `_require_permission(permission: str)` 依赖函数
- 检查 `request.state.current_user` 是否拥有指定权限
- 无权限 → 403

#### Task 17：后端测试
- 文件：`backend/tests/test_rbac.py`
- 用户 CRUD 测试
- 角色 CRUD 测试
- 权限校验 403 测试
- 超级管理员种子数据测试
- 登录测试（新用户 / 旧 admin 兼容）

---

### PR-2：前端改造

**范围：** 新增用户管理页 + 角色管理页 + 登录页改造 + 菜单按权限过滤 + 项目列表按分配过滤

**依赖：** PR-1 已合并

#### Task 18：新增 PageKey + api.ts 类型
- `PageKey` 新增 `'users'` | `'roles'`
- `api.ts` 新增所有 RBAC 相关类型：`User`, `Role`, `RoleCreatePayload`, `UserCreatePayload` 等
- `api.ts` 新增 API 函数：`fetchUsers`, `createUser`, `updateUser`, `deleteUser`, `fetchRoles`, `createRole`, `updateRole`, `deleteRole`, `assignProjects`

#### Task 19：新增用户管理页
- 文件：`frontend/src/pages/UsersPage.tsx`
- 列表 + 新建/编辑弹窗（Dialog）
- 弹窗含：用户名、密码（新建时必填）、显示名、角色选择（下拉）、项目分配（多选 checkboxes）、启用开关

#### Task 20：新增角色管理页
- 文件：`frontend/src/pages/RolesPage.tsx`
- 列表 + 新建/编辑弹窗
- 弹窗含：角色名、描述、权限勾选（按分组：菜单权限 + 操作权限）

#### Task 21：改造 AppShell 导航
- 侧栏新增「系统管理」分组，含「用户管理」「角色管理」
- 图标：`Users` / `Shield`
- 按 `currentUser.permissions` 过滤可见菜单项

#### Task 22：改造 App.tsx 集成
- 登录后调用 `GET /api/auth/me` 获取当前用户信息（含角色、权限、分配项目）
- `currentUser` 状态在所有页面间共享
- 项目列表按 `currentUser.assigned_project_ids` 过滤（超级管理员看全部）
- 菜单 `navItems` 按 `currentUser.permissions` 过滤

#### Task 23：改造登录页
- 用户名从 `admin` 改为用户自己输入
- 登录成功后显示 `display_name` 而非固定 `admin`

#### Task 24：AppShell 用户区显示当前用户
- 显示 `display_name` 或 `username`
- 头像首字母取自 `display_name`

#### Task 25：前端测试
- `UsersPage` 渲染测试
- `RolesPage` 渲染测试
- 菜单过滤测试
- 项目过滤测试

---

### PR-3：权限拦截生效 + 全局兜底

**范围：** 为所有现有 API 路由添加 `_require_permission` 依赖，webhook 不受影响

#### Task 26：admin.py 路由加权限守卫
- 按路由逐个添加 `_require_permission("page:xxx")` 依赖
- 例如：`list_providers` → `_require_permission("page:providers")`
- 例如：`create_user` → `_require_permission("user:create")`

#### Task 27：确认 webhook 路由不受影响
- `gitlab_webhook.py` 使用 `X-Internal-Token` 认证，不经过用户 JWT
- 确认无改动

#### Task 28：ENV 开关兼容
- 新增 `RBAC_ENABLED` ENV 变量（默认 `True`）
- 设为 `False` 时回退到旧版单账号模式（所有路由跳过权限校验）

#### Task 29：端到端测试
- 新建 viewer 角色用户 → 登录 → 只能看到仪表盘+审查记录+问题与误报
- 分配项目后 → 项目列表只显示分配的项目

---

## 执行策略

1. **PR-1 先合并**（后端全量），后端测试全绿
2. **PR-2 再合并**（前端），npm test + npm run build 全绿
3. **PR-3 最后合并**（权限拦截生效），end-to-end 验证

每个 PR 独立可部署，不破坏现有功能。