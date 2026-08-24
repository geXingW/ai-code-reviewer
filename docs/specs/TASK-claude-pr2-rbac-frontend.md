# PR-2：RBAC 前端改造 — 用户管理 + 角色管理 + 权限过滤

## 绝对约束

- **不要修改后端代码**（`backend/` 下任何文件）
- **不要修改现有测试文件**（`*.test.*` 一律不动）
- **不要修改 pyproject.toml / tsconfig.json / vite.config.ts 等配置文件**
- **不要修改现有页面组件的业务逻辑**（只扩展，不改内部逻辑）
- **CSS 只使用 Tailwind utility classes + cn()**，不引入新 CSS 文件
- **不要引入新的 npm 依赖**（只用项目已有的 `lucide-react`, `@/components/ui/*` 等）

## 文件清单

### 新建文件（3 个）

1. `frontend/src/pages/UsersPage.tsx` — 用户管理页
2. `frontend/src/pages/RolesPage.tsx` — 角色管理页
3. `frontend/src/pages/SystemPage.tsx` — 系统管理页（Tab 切换用户/角色）

### 修改文件（4 个）

4. `frontend/src/api.ts` — 新增 RBAC 类型和 API 函数
5. `frontend/src/components/layout/AppShell.tsx` — 新增 PageKey + 导航分组 + 权限过滤 + 用户区改
6. `frontend/src/App.tsx` — 集成 currentUser + 菜单/项目过滤 + 登录后调用 /api/auth/me
7. `frontend/src/pages/LoginPage.tsx` — 登录页文案调整（"管理员账号" → "用户名"）

---

## 后端 API 现状（前端需要对接的接口）

以下 API 已在 PR-1 中实现，前端直接调用即可：

### 认证
- `POST /api/auth/login` — 返回 `{ access_token, token_type, expires_in, username, display_name, permissions: string[], project_ids: string[] }`

### 用户管理
- `GET /api/users?offset=0&limit=20` — 分页列表，返回 `{ items: User[], total, limit, offset }`
- `POST /api/users` — 创建用户，body: `{ username, password, display_name?, role_id?, enabled? }`
- `GET /api/users/{id}` — 用户详情
- `PATCH /api/users/{id}` — 更新用户，body: `{ username?, password?, display_name?, role_id?, enabled? }`
- `DELETE /api/users/{id}` — 软删除（设 enabled=false）
- `PUT /api/users/{id}/projects` — 分配项目，body: `{ project_ids: string[] }`

### 角色管理
- `GET /api/roles` — 列表，返回 `{ items: Role[], total, limit, offset }`
- `POST /api/roles` — 创建角色，body: `{ name, description?, permissions: string[] }`
- `GET /api/roles/{id}` — 角色详情
- `PATCH /api/roles/{id}` — 更新角色，body: `{ name?, description?, permissions? }`
- `DELETE /api/roles/{id}` — 删除角色（is_system 角色不可删）

### 用户数据类型
```typescript
type User = {
  id: string;
  username: string;
  display_name: string | null;
  role_id: string | null;
  role_name: string | null;
  enabled: boolean;
  assigned_project_ids: string[];
  created_at: string;
  updated_at: string;
};

type Role = {
  id: string;
  name: string;
  description: string | null;
  is_system: boolean;
  permissions: string[];
  created_at: string;
  updated_at: string;
};
```

---

## 详细任务

### Task 18: api.ts 新增 RBAC 类型和 API 函数

**文件：`frontend/src/api.ts`**

#### 18a. 新增类型定义

在文件顶部（`HealthStatus` 定义附近）新增：

```typescript
export type User = {
  id: string;
  username: string;
  display_name: string | null;
  role_id: string | null;
  role_name: string | null;
  enabled: boolean;
  assigned_project_ids: string[];
  created_at: string;
  updated_at: string;
};

export type Role = {
  id: string;
  name: string;
  description: string | null;
  is_system: boolean;
  permissions: string[];
  created_at: string;
  updated_at: string;
};

export type UserCreatePayload = {
  username: string;
  password: string;
  display_name?: string;
  role_id?: string;
  enabled?: boolean;
};

export type UserUpdatePayload = {
  username?: string;
  password?: string;
  display_name?: string;
  role_id?: string;
  enabled?: boolean;
};

export type UserProjectAssignPayload = {
  project_ids: string[];
};

export type RoleCreatePayload = {
  name: string;
  description?: string;
  permissions: string[];
};

export type RoleUpdatePayload = {
  name?: string;
  description?: string;
  permissions?: string[];
};
```

#### 18b. 修改 LoginResponse 类型

将现有的 `LoginResponse` 类型扩展为包含 `display_name`, `permissions`, `project_ids`：

```typescript
export type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  username: string;
  display_name: string | null;
  permissions: string[];
  project_ids: string[];
};
```

#### 18c. 新增 CurrentUser 类型

```typescript
export type CurrentUser = {
  username: string;
  display_name: string | null;
  permissions: string[];
  project_ids: string[];
};
```

#### 18d. 新增 API 函数

在文件末尾（`deleteRule` 等函数附近）新增：

```typescript
// ── 用户管理 ──

export async function fetchUsers(offset = 0, limit = 20): Promise<Page<User>> {
  return apiGet(`/api/users?offset=${offset}&limit=${limit}`);
}

export async function fetchUser(id: string): Promise<User> {
  return apiGet(`/api/users/${id}`);
}

export async function createUser(payload: UserCreatePayload): Promise<User> {
  return apiPost('/api/users', payload);
}

export async function updateUser(id: string, payload: UserUpdatePayload): Promise<User> {
  return apiPatch(`/api/users/${id}`, payload);
}

export async function deleteUser(id: string): Promise<void> {
  return apiDelete(`/api/users/${id}`);
}

export async function assignProjects(id: string, payload: UserProjectAssignPayload): Promise<User> {
  return apiPut(`/api/users/${id}/projects`, payload);
}

// ── 角色管理 ──

export async function fetchRoles(offset = 0, limit = 50): Promise<Page<Role>> {
  return apiGet(`/api/roles?offset=${offset}&limit=${limit}`);
}

export async function fetchRole(id: string): Promise<Role> {
  return apiGet(`/api/roles/${id}`);
}

export async function createRole(payload: RoleCreatePayload): Promise<Role> {
  return apiPost('/api/roles', payload);
}

export async function updateRole(id: string, payload: RoleUpdatePayload): Promise<Role> {
  return apiPatch(`/api/roles/${id}`, payload);
}

export async function deleteRole(id: string): Promise<void> {
  return apiDelete(`/api/roles/${id}`);
}

// ── 当前用户 ──

export async function fetchCurrentUser(): Promise<CurrentUser> {
  return apiGet('/api/auth/me');
}
```

#### 18e. 修改 loginAdmin 函数

登录成功后，除了存储 `access_token` 和 `username`，还需要存储 `display_name`, `permissions`, `project_ids` 到 sessionStorage：

```typescript
export async function loginAdmin(username: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `登录失败 (${res.status})`);
  }
  const data: LoginResponse = await res.json();
  // 存储到 sessionStorage
  sessionStorage.setItem('admin_access_token', data.access_token);
  sessionStorage.setItem('admin_username', data.username);
  sessionStorage.setItem('admin_display_name', data.display_name || data.username);
  sessionStorage.setItem('admin_permissions', JSON.stringify(data.permissions));
  sessionStorage.setItem('admin_project_ids', JSON.stringify(data.project_ids));
  return data;
}
```

新增辅助函数：

```typescript
export function getStoredAdminPermissions(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem('admin_permissions') || '[]');
  } catch {
    return [];
  }
}

export function getStoredAdminProjectIds(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem('admin_project_ids') || '[]');
  } catch {
    return [];
  }
}

export function getStoredAdminDisplayName(): string {
  return sessionStorage.getItem('admin_display_name') || '';
}
```

---

### Task 19: UsersPage 用户管理页

**文件：`frontend/src/pages/UsersPage.tsx`**

**页面结构：**
- 顶部：标题 "用户管理" + 右侧 "新建用户" 按钮
- 表格：用户名 | 显示名 | 角色 | 分配项目数 | 状态 | 操作
- 每行操作：编辑按钮、删除按钮
- 新建/编辑弹窗（Dialog）：用户名、密码（新建时必填）、显示名、角色下拉选择、项目多选 checkboxes、启用开关

**参考模板：** 参考 `frontend/src/components/UserMappingsPage.tsx` 的组件风格（Card + Dialog + 表格 + EmptyState）。

**组件 Props：**
```typescript
interface UsersPageProps {
  projects: ProjectConfig[];
}
```

**实现要点：**
- 使用 `fetchUsers()` 加载列表，分页
- 使用 `fetchRoles()` 获取角色列表用于下拉选择
- 创建用户：`createUser(payload)`
- 更新用户：`updateUser(id, payload)`
- 删除用户：`deleteUser(id)`（调用后刷新列表）
- 分配项目：`assignProjects(id, { project_ids })`
- 删除前弹出 `window.confirm` 确认
- 系统内置用户（username === 'admin'）不可删除，编辑按钮可用但删除按钮 disabled

**状态管理：**
```typescript
const [users, setUsers] = useState<User[]>([]);
const [roles, setRoles] = useState<Role[]>([]);
const [loading, setLoading] = useState(true);
const [error, setError] = useState<string | null>(null);
const [dialog, setDialog] = useState<...>(null); // 同 UserMappingsPage 的 dialog 模式
const [page, setPage] = useState({ offset: 0, limit: 20, total: 0 });
```

**文档注释：** 文件顶部写清楚 JSDoc 描述组件用途。

---

### Task 20: RolesPage 角色管理页

**文件：`frontend/src/pages/RolesPage.tsx`**

**页面结构：**
- 顶部：标题 "角色管理" + 右侧 "新建角色" 按钮
- 表格：角色名 | 描述 | 权限数 | 系统角色 | 操作
- 每行操作：编辑按钮、删除按钮（is_system 角色不可删）
- 新建/编辑弹窗（Dialog）：角色名、描述、权限勾选（按分组）

**权限分组展示：**
```
菜单权限：
  ☑ page:dashboard   ☑ page:providers   ☑ page:global-prompt
  ☑ page:rules       ☑ page:projects     ☑ page:user-mappings
  ☑ page:reviews     ☑ page:findings     ☑ page:falsePositives
  ☑ page:negativeExamples  ☑ page:engines
  ☑ page:users       ☑ page:roles

操作权限：
  ☑ user:create  ☑ user:edit  ☑ user:delete
  ☑ role:create  ☑ role:edit  ☑ role:delete
  ☑ project:assign
```

**权限 label 映射：**
```typescript
const PERMISSION_LABELS: Record<string, string> = {
  'page:dashboard': '仪表盘',
  'page:providers': '模型供应商',
  'page:global-prompt': '全局提示词',
  'page:rules': '审查规则',
  'page:projects': 'GitLab 项目',
  'page:user-mappings': '用户映射',
  'page:reviews': '审查记录',
  'page:findings': '问题与误报',
  'page:falsePositives': '误报队列',
  'page:negativeExamples': '负样本库',
  'page:engines': '引擎配置',
  'page:users': '用户管理',
  'page:roles': '角色管理',
  'user:create': '创建用户',
  'user:edit': '编辑用户',
  'user:delete': '删除用户',
  'role:create': '创建角色',
  'role:edit': '编辑角色',
  'role:delete': '删除角色',
  'project:assign': '分配项目',
};
```

**实现要点：**
- 使用 `fetchRoles()` 加载列表
- 创建角色：`createRole(payload)`
- 更新角色：`updateRole(id, payload)`
- 删除角色：`deleteRole(id)`（调用后刷新列表）
- 删除前弹出 `window.confirm` 确认
- is_system 角色删除按钮 disabled + tooltip 提示

**Props：** 无外部依赖（不需要 projects）

---

### Task 21: 改造 AppShell 导航

**文件：`frontend/src/components/layout/AppShell.tsx`**

#### 21a. PageKey 新增

```typescript
export type PageKey =
  | 'dashboard'
  | 'providers'
  | 'global-prompt'
  | 'rules'
  | 'projects'
  | 'user-mappings'
  | 'reviews'
  | 'findings'
  | 'falsePositives'
  | 'negativeExamples'
  | 'engines'
  | 'users'        // ← 新增
  | 'roles';       // ← 新增
```

#### 21b. 新增「系统管理」导航分组

在 `NAV_SECTIONS` 末尾新增：

```typescript
{
  label: '系统管理',
  items: [
    { key: 'users', label: '用户管理', icon: Users },
    { key: 'roles', label: '角色管理', icon: Shield },
  ],
},
```

需要新增 import：`import { Shield } from 'lucide-react';`

#### 21c. AppShell Props 新增 currentUser 和 permissions

```typescript
interface AppShellProps {
  activePage: PageKey;
  onNavigate: (page: PageKey) => void;
  health: { status: string; version?: string } | null;
  onLogout: () => void;
  children: React.ReactNode;
  // 新增
  currentUser?: { username: string; display_name: string | null };
  permissions?: string[];
}
```

`currentUser` 和 `permissions` 为可选参数，默认值分别为 `undefined` 和 `[]`。

#### 21d. 按权限过滤导航菜单

在渲染 `NAV_SECTIONS` 时，过滤掉用户没有权限的菜单项：

```typescript
// 过滤函数：如果 permissions 为空数组（未传入），显示全部菜单
const hasPermission = (pageKey: PageKey): boolean => {
  if (!permissions || permissions.length === 0) return true;
  const permKey = `page:${pageKey}`;
  return permissions.includes(permKey);
};
```

渲染时：
```typescript
{NAV_SECTIONS.map((section) => {
  const visibleItems = section.items.filter((item) => hasPermission(item.key));
  if (visibleItems.length === 0) return null; // 整组无可见项则隐藏
  return (
    <div key={section.label}>
      ...
      {visibleItems.map((item) => ...)}
    </div>
  );
})}
```

#### 21e. 用户区显示当前用户

替换底部硬编码的 `admin` / `Bearer Token`：

```tsx
<div className="flex w-full items-center gap-2 rounded-md p-1.5 text-left">
  <div className="flex size-6 shrink-0 items-center justify-center rounded-full bg-indigo-500 text-[11px] font-medium text-white">
    {(currentUser?.display_name || currentUser?.username || 'A').charAt(0).toUpperCase()}
  </div>
  <div className="min-w-0 flex-1">
    <div className="truncate text-[12px] font-medium leading-tight text-zinc-900">
      {currentUser?.display_name || currentUser?.username || 'admin'}
    </div>
    <div className="truncate text-[11px] leading-tight text-zinc-500">
      {currentUser?.username ? `@${currentUser.username}` : 'Bearer Token'}
    </div>
  </div>
  <MoreHorizontalIcon className="size-3.5 shrink-0 text-zinc-400" />
</div>
```

---

### Task 22: 改造 App.tsx 集成

**文件：`frontend/src/App.tsx`**

#### 22a. 新增 import

```typescript
import {
  // ... existing imports
  getStoredAdminPermissions,
  getStoredAdminProjectIds,
  getStoredAdminDisplayName,
  fetchCurrentUser,
  type CurrentUser,
} from './api';
import { UsersPage } from './pages/UsersPage';
import { RolesPage } from './pages/RolesPage';
```

#### 22b. 新增 currentUser 状态

在组件顶部新增状态：

```typescript
const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
```

#### 22c. 登录成功后加载 currentUser

在 `handleLogin` 中，登录成功后调用 `fetchCurrentUser()` 或直接从 `loginAdmin` 返回值构建：

```typescript
// 登录成功后，从返回值构建 currentUser
const loginData = await loginAdmin(loginForm.username, loginForm.password);
setCurrentUser({
  username: loginData.username,
  display_name: loginData.display_name,
  permissions: loginData.permissions,
  project_ids: loginData.project_ids,
});
```

#### 22d. 页面恢复时从 sessionStorage 恢复 currentUser

在 `useEffect` 初始化时（已有 `adminToken` 检查），如果已登录，从 sessionStorage 恢复：

```typescript
if (adminToken) {
  setCurrentUser({
    username: getStoredAdminUsername() || '',
    display_name: getStoredAdminDisplayName() || null,
    permissions: getStoredAdminPermissions(),
    project_ids: getStoredAdminProjectIds(),
  });
}
```

#### 22e. 传递 currentUser 和 permissions 给 AppShell

```tsx
<AppShell
  activePage={activePage}
  onNavigate={handleNavigate}
  health={health}
  onLogout={handleLogout}
  currentUser={currentUser ? { username: currentUser.username, display_name: currentUser.display_name } : undefined}
  permissions={currentUser?.permissions}
>
```

#### 22f. 项目列表按权限过滤

在加载项目列表后，如果用户不是超级管理员（`permissions` 不包含 `page:users`），则按 `currentUser.project_ids` 过滤：

```typescript
const filteredProjects = useMemo(() => {
  if (!currentUser || currentUser.permissions.includes('page:users')) {
    return projects; // 超级管理员看全部
  }
  return projects.filter((p) => currentUser.project_ids.includes(p.id));
}, [projects, currentUser]);
```

将所有 `projects` 传递给子组件的地方改为 `filteredProjects`。

#### 22g. handleNavigate 中新增 users/roles 路由

在 `handleNavigate` 函数中新增两个 case：

```typescript
case 'users':
  setActivePage('users');
  break;
case 'roles':
  setActivePage('roles');
  break;
```

#### 22h. 渲染 UsersPage 和 RolesPage

在 `renderPage()` 函数中新增：

```typescript
case 'users':
  return <UsersPage projects={filteredProjects} />;
case 'roles':
  return <RolesPage />;
```

#### 22i. 登出时清除 currentUser

在 `handleLogout` 中：

```typescript
setCurrentUser(null);
// ... 现有的 clearStoredAdminAccessToken 逻辑
```

---

### Task 23: 改造登录页

**文件：`frontend/src/pages/LoginPage.tsx`**

仅改文案，不改逻辑：

- 第 39 行：`<Label htmlFor="login-username">管理员账号</Label>` → `用户名`
- 第 47 行：`<Label htmlFor="login-password">管理员密码</Label>` → `密码`
- 第 34 行：`登录后管理 API 将携带 Bearer Token` → `请输入用户名和密码登录管理台`

---

### Task 24: AppShell 用户区显示当前用户

已在 Task 21e 中完成。

---

### Task 25: 前端测试

**文件：`frontend/src/App.test.tsx`**（谨慎修改，只改受影响的断言）

#### 25a. 登录页测试更新

如果现有测试中断言了 "管理员账号" 或 "管理员密码" 文本，改为 "用户名" 或 "密码"。

#### 25b. 新增 UsersPage 和 RolesPage 渲染测试

**新建文件：`frontend/src/pages/UsersPage.test.tsx`**

```typescript
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { UsersPage } from './UsersPage';

describe('UsersPage', () => {
  it('renders the page title', () => {
    render(<UsersPage projects={[]} />);
    expect(screen.getByText('用户管理')).toBeDefined();
  });

  it('shows empty state when no users', async () => {
    render(<UsersPage projects={[]} />);
    // 等待 loading 结束后检查空状态
    expect(await screen.findByText(/暂无/)).toBeDefined();
  });
});
```

**新建文件：`frontend/src/pages/RolesPage.test.tsx`**

```typescript
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RolesPage } from './RolesPage';

describe('RolesPage', () => {
  it('renders the page title', () => {
    render(<RolesPage />);
    expect(screen.getByText('角色管理')).toBeDefined();
  });
});
```

---

## 执行顺序

1. **Task 18**（api.ts）— 先完成类型和 API 函数，所有后续页面依赖它
2. **Task 19**（UsersPage）— 用户管理页
3. **Task 20**（RolesPage）— 角色管理页
4. **Task 21**（AppShell）— 导航改造
5. **Task 22**（App.tsx）— 集成 currentUser + 菜单/项目过滤
6. **Task 23**（LoginPage）— 文案调整
7. **Task 25**（测试）— 最后更新测试

Task 24 已包含在 Task 21 中。

## 验证标准

- `npm run build` 无 TypeScript 错误
- `npm test` 全部通过（已有的测试不破坏）
- 登录后侧栏出现「系统管理」分组，含「用户管理」「角色管理」
- 用户管理页可 CRUD 用户
- 角色管理页可 CRUD 角色
- 非超级管理员登录后，菜单和项目列表按权限过滤
- 用户区显示当前登录用户的 display_name