# 前端改动 Spec：用户映射页面 + 移除首页手动触发

**项目路径**：`/home/ubuntu/projects/ai-code-reviewer/frontend`
**后端 API 已就绪**（PR #137 合入 master），只需前端接入。

---

## 改动一：新增「用户映射」独立页面

### 目标
在左侧导航栏新增「用户映射」页面，用于管理各项目的 GitLab 用户名 ↔ 钉钉手机号映射关系（钉钉通知 @MR 创建人功能的配置入口）。

### 页面结构
- **顶部**：项目选择器（下拉，列出所有已配置的 GitLab 项目，默认选中第一个）
- **主体**：映射列表表格，列如下：
  | 列名 | 说明 |
  |------|------|
  | GitLab 用户名 | `gitlab_username` |
  | 钉钉手机号 | `dingtalk_mobile` |
  | 钉钉 UserID（可选） | `dingtalk_userid` |
  | 显示名称（可选） | `display_name` |
  | 操作 | 编辑 / 删除 |
- **右上角**：「+ 添加映射」按钮，点击弹出新增表单
- **空态**：项目无映射时展示空态提示

### 新增/编辑表单（弹窗内）
字段：
- GitLab 用户名（必填）
- 钉钉手机号（必填）
- 钉钉 UserID（选填）
- 显示名称（选填）

### 后端 API（已存在，直接调用）
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/projects/{project_id}/user-mappings` | 列出项目下所有映射 |
| POST | `/api/projects/{project_id}/user-mappings` | 新增映射 |
| PUT | `/api/user-mappings/{mapping_id}` | 更新映射 |
| DELETE | `/api/user-mappings/{mapping_id}` | 删除映射 |

请求/响应 schema：
```ts
type UserMapping = {
  id: string;
  project_id: string;
  gitlab_username: string;
  dingtalk_mobile: string;
  dingtalk_userid: string | null;
  display_name: string | null;
  created_at: string;
  updated_at: string;
};

type UserMappingCreatePayload = {
  gitlab_username: string;
  dingtalk_mobile: string;
  dingtalk_userid?: string | null;
  display_name?: string | null;
};

type UserMappingUpdatePayload = {
  gitlab_username?: string;
  dingtalk_mobile?: string;
  dingtalk_userid?: string | null;
  display_name?: string | null;
};
```

### 接入方式（严格遵循现有项目模式）

1. **`src/api.ts`**：在通知渠道 API 下方新增用户映射相关的 type 和 API 函数（`fetchUserMappings` / `createUserMapping` / `updateUserMapping` / `deleteUserMapping`），统一用 `adminFetch`。

2. **`src/App.tsx`**：
   - `PageKey` 新增 `'user-mappings'`
   - `navItems` 新增 `{ key: 'user-mappings', label: '用户映射' }`（放在「GitLab 项目」之后、「审查记录」之前）
   - 新增对应渲染分支：`{activePage === 'user-mappings' ? renderUserMappings() : null}`
   - 新增 `renderUserMappings()` 函数（页面内容）
   - 新增相关 state：`userMappings`、`userMappingsLoading`、`userMappingsError`、`selectedProjectId`
   - 新增弹窗 state：`userMappingDialog`（`null | { mode: 'create' } | { mode: 'edit', data: UserMapping }`）

3. **组件位置**：因为 App.tsx 已经很胖，**新建组件文件** `src/components/UserMappingsPage.tsx`，把页面逻辑（列表、弹窗、CRUD）封装在组件里，`renderUserMappings()` 只做简单调用。组件签名：
   ```tsx
   interface UserMappingsPageProps {
     projects: ProjectConfig[];  // 从 props 传入项目列表，复用 App 已加载的数据
   }
   ```
   如果 projects 列表还没加载好（null/空），显示加载态。

4. **样式**：沿用现有 UI 组件（Button / Input / Dialog / 表格风格），和其他页面保持一致的视觉语言。参考「审查规则」或「模型供应商」页面的表格样式。

---

## 改动二：移除首页「手动触发 MR 审查」模块

### 移除范围
从 `src/App.tsx` 中完整移除手动触发 MR 审查相关的所有代码：

1. **类型与常量**（删除）：
   - `type FormState`（第 105-115 行左右）
   - `const initialForm`（第 117 行起）
   - 注意：`FormState` 这个名字可能和登录表单的 `LoginFormState` 混淆，但它就是手动触发的表单，确认删除。

2. **State**（删除）：
   - `const [form, setForm] = useState<FormState>(initialForm);`
   - `const [submitting, setSubmitting] = useState(false);` —— **注意**：`submitting` 在登录页也用了（第 849 行），所以不能直接删这个 state。实际上手动触发的 submitting 和登录的 submitting 是同一个变量复用，需要拆分：登录页用独立的 `loginSubmitting` state，然后删除手动触发相关的用法。
   - `const [submitResult, setSubmitResult] = useState<CreateReviewResponse | null>(null);`

3. **函数**（删除）：
   - `handleRefreshReviews()` 函数
   - `handleSubmit()` 函数（手动触发的提交函数）

4. **JSX**（删除）：
   - 整个 `{/* 手动触发 MR 审查 */}` section（第 1179-1219 行左右）

5. **Import 清理**：
   - 移除 `CreateReviewPayload`、`CreateReviewResponse`、`createReview` 的 import
   - 移除 `parsePositiveInteger` 工具函数（如果没有其他地方使用的话，先检查）—— 检查后如果只被手动触发使用则删除，否则保留。

6. **测试文件**：
   - `src/App.test.tsx` 中 `it('可通过表单手动触发一次 MR 审查', ...)` 这个测试用例删除

### 注意事项
- **`submitting` 变量**：登录页也用了同一个 `submitting` state（第 849 行 `submitting={submitting}`）。需要新增 `loginSubmitting` state 给登录页专用，然后删除手动触发相关的 `setSubmitting` 调用。
- **`error` / `message` state**：是全局共用的，别动。
- **仔细检查** `parsePositiveInteger` 函数是否还有其他调用方，没有就删，有就留。

---

## 交付标准
1. `npm run build` 编译通过
2. `npm run lint` 无新增 error
3. 前端页面能正常访问 `#/user-mappings` 路由
4. 首页不再显示「手动触发 MR 审查」卡片
5. 用户映射页面的增删改查功能完整（mock 或真实 API 均可验证交互）
