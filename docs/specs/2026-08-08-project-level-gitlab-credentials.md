# Spec: GitLab Webhook 项目级凭证改造 + Release SQL 归档

## 背景

当前 webhook 链路有两个全局 ENV 依赖，不符合多项目/多 GitLab 实例场景：

1. Webhook secret 校验读 `GITLAB_WEBHOOK_SECRET`（ENV 级），应该读项目级 `project.webhook_secret`
2. GitLab API 调用读 `GITLAB_TOKEN`（ENV 级），应该读项目级 `project.gitlab_access_token`
3. GitLab base URL 也是全局 ENV，需要下沉到项目级，支持对接多个 GitLab 实例

另外，release 包需要附带可追溯的 SQL 版本文件和全量初始化 SQL，便于离线部署/升级。

---

## 一、Project 模型新增 `gitlab_base_url` 字段

### 后端模型
- 文件：`backend/models/project.py`
- 新增字段：`gitlab_base_url: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")`
  - 普通字符串，非敏感，不需要加密
  - 给个空串 server_default 避免老数据 migration 报错

### Alembic migration
- 新建：`backend/alembic/versions/0007_project_gitlab_base_url.py`
- 只加列，不删除任何列（不破坏向后兼容）

### Schema
- 文件：`backend/schemas/project.py`
- `ProjectCreate`：新增 `gitlab_base_url: str`，必填
- `ProjectUpdate`：新增 `gitlab_base_url: str | None = None`
- `ProjectRead`：新增 `gitlab_base_url: str`
- `ProjectConfig`（如果有独立的返回 schema）：同步新增

### Admin API
- 文件：`backend/api/admin.py`
- 创建/更新项目时透传 `gitlab_base_url`
- webhook 自动配置接口（`_setup_gitlab_webhook` 之类）：用 `project.gitlab_base_url` 替代 `settings.gitlab_base_url`
- webhook 卸载/测试连接接口同理

---

## 二、Webhook 入口改为项目级校验

### 文件：`backend/api/gitlab_webhook.py`

改造后的处理流程：

```
POST /api/webhooks/gitlab
  1. 从 payload 解析 project.id（GitLab 数值 ID）
     - 解析失败 → 422（复用现有 _parse_merge_request_event 的前半段）
  2. 查 DB：project_repo.get_by_gitlab_project_id(str(project_id))
     - 项目不存在 → 401，detail="Invalid webhook token"（不泄露项目存在性）
     - 项目存在但 disabled → 202，processed=false，reason="project_disabled"
  3. 用 project.webhook_secret 校验 X-Gitlab-Token 头
     - hmac.compare_digest 常量时间比较
     - 校验失败 → 401，detail="Invalid webhook token"
  4. 用 project.gitlab_base_url + project.gitlab_access_token 构造 GitLabClient
  5. 解析完整 MR event，调用 review_merge_request_event（传入 client 和 project）
```

### 关键改动点

1. **`_validate_webhook_secret` 函数**：签名改为 `_validate_webhook_secret(token: str | None, project: Project) -> None`，不再读 settings
2. **`review_merge_request_event` 函数**：增加 `project: Project` 参数，用 `project.gitlab_base_url` 和 `project.gitlab_access_token` 构造 `GitLabClient`，替代 settings 中的全局值
3. **新增 `_resolve_project` 辅助函数**：从 payload 提取 project_id 并查库，统一处理 401/422
4. **`_parse_merge_request_event`**：保留现有逻辑，仍然做完整校验（因为 project_id 的提取可以先做轻量解析，event 完整解析放后面）

### 关于轻量解析 project_id

为了在校验 secret 之前就知道是哪个项目，需要从 payload 里提前拿到 `project.id`。可以把这部分抽成一个独立函数 `_extract_project_id(payload) -> int`，只做最小解析（不校验其他字段）。

---

## 三、统一 GitLabClient 构造入口

### 新增工厂函数
- 位置：`backend/integrations/gitlab/client.py`（或新建 `backend/services/gitlab_client_factory.py`）
- 签名：`def build_gitlab_client_for_project(project: Project) -> GitLabClient`
- 职责：从 project 提取 base_url + access_token，构造 GitLabClient
- **所有需要按项目构造 GitLabClient 的地方都调用这个工厂**，包括：
  - `gitlab_webhook.py` 的 webhook 处理
  - `admin.py` 的 webhook 配置/测试连接
  - `review_orchestrator.py` 内部如果有动态构造的地方（目前是外部注入，保持不变）

ReviewOrchestrator 仍然通过构造函数注入 `gitlab_client`（方案 B），工厂函数只在 orchestrator 之外的入口层使用。

---

## 四、Config 层清理

### 文件：`backend/core/config.py`
- 删除 `gitlab_base_url` 字段
- 删除 `gitlab_token` 字段
- 删除 `gitlab_webhook_secret` 字段

### 文件：`.env.example`
- 删除 `GITLAB_BASE_URL`、`GITLAB_TOKEN`、`GITLAB_WEBHOOK_SECRET` 三行
- 在注释里说明这些配置已下沉到项目级

### 文档更新
- `docs/setup.md`：删除 Docker run 和 .env 示例中的 GITLAB_* 变量
- `README.md`：同步更新配置说明
- `docs/gitlab-setup.md`：检查是否有引用，更新为项目级配置说明

---

## 五、前端同步

### 文件：`frontend/src/api.ts`
- `ProjectConfig`：新增 `gitlab_base_url: string`
- `ProjectFormPayload`：新增 `gitlab_base_url: string`
- `ProjectUpdatePayload`：新增 `gitlab_base_url?: string`

### 文件：`frontend/src/components/dialogs/ProjectDialog.tsx`
- form 初始值增加 `gitlab_base_url: ''`
- 编辑时回填 `gitlab_base_url`
- 新增一个表单项：GitLab Base URL（输入框，placeholder 如 `https://gitlab.example.com`）
- 放在 GitLab Project ID 上面或下面，作为项目的基础地址

### 测试
- `frontend/src/api.test.ts`：mock 数据增加 `gitlab_base_url` 字段
- `frontend/src/App.test.ts`：检查是否有 project 相关 mock 需要更新

---

## 六、测试调整

### `backend/tests/test_gitlab_webhook.py`

所有用例需要在 DB 中预置 Project 记录才能通过 secret 校验。

1. **`test_gitlab_webhook_rejects_invalid_secret`**：
   - 预置 project（webhook_secret="test-webhook-secret"），传 wrong token → 401
   - 新增用例：项目不存在（project_id 不匹配）→ 401
   - 新增用例：项目存在但 disabled → 202 + processed=false

2. **`test_gitlab_webhook_ignores_non_merge_request_events`**：
   - 预置 project，验证非 MR 事件仍然返回 202 + processed=false

3. **`test_gitlab_webhook_dispatches_supported_merge_request`**：
   - 预置 project，验证正常流程通过
   - 验证 `review_merge_request_event` 收到的 client 使用的是项目级 token（可以通过 monkeypatch 捕获构造参数）

4. **`test_gitlab_webhook_dispatches_mr_close_action`**：
   - 同上，预置 project

### `backend/tests/test_gitlab_webhook_config.py`
- 检查用例中是否用了 settings.gitlab_base_url / settings.gitlab_token
- 改为使用 project 上的字段

### `backend/tests/test_api_crud.py`
- 创建项目的 payload 增加 `gitlab_base_url`
- 验证返回结果包含 `gitlab_base_url`

### 新增测试：项目级 client 工厂
- 验证 `build_gitlab_client_for_project` 正确使用 project 的 base_url 和 access_token

---

## 七、Release SQL 归档

### 目标
在 release 包（zip/wheel 或 GitHub Release Assets）中附带：
1. **全量初始化 SQL**（`schema-full.sql`）：从零建库，包含所有表 + 初始数据
2. **增量版本 SQL**（每个 migration 一个 `.sql` 文件）：可追溯每个版本的 DDL 变更
3. **VERSION 文件**：记录当前 release 对应的 alembic head version

### 实现方式

新增脚本：`backend/scripts/generate_release_sql.py`

功能：
1. 利用 alembic 的 offline mode（`alembic upgrade head --sql`）生成全量 DDL
2. 逐个 migration 生成增量 SQL（`alembic upgrade <prev>:<curr> --sql`）
3. 输出到 `backend/sql/` 目录：
   ```
   sql/
   ├── VERSION                    # 当前 head 的 revision number
   ├── schema-full.sql            # 全量初始化 SQL（从空库到 head）
   └── migrations/
       ├── 0001_initial_schema.sql
       ├── 0002_incremental_review.sql
       ├── 0003_review_lifecycle_event.sql
       ├── 0004_finding_and_rule_category.sql
       ├── 0005_project_notification_channel.sql
       ├── 0006_rule_tags.sql
       └── 0007_project_gitlab_base_url.sql
   ```

### 集成到打包流程
- 在 `Dockerfile` 中增加一步：运行 `python scripts/generate_release_sql.py`，把 `sql/` 目录打进镜像
- wheel 包通过 `package-data` 配置把 `sql/` 包含进去
  - `pyproject.toml` 的 `[tool.setuptools.package-data]` 增加 `sql/**/*`

### 使用说明
- 新部署：执行 `schema-full.sql` 建库，不需要跑 alembic
- 升级：按版本号顺序执行 `migrations/` 中未应用过的 SQL 文件，或继续用 `alembic upgrade head`

---

## 八、边界与注意事项

1. **加密字段不变**：`gitlab_access_token` 和 `webhook_secret` 继续用 `EncryptedString`，`gitlab_base_url` 是明文
2. **Migration 只加不删**：`GITLAB_TOKEN` 等 ENV 字段从 config 删除，但 DB 的 migration 只加 `gitlab_base_url` 列，不删任何列
3. **错误路径测试**：webhook 必须覆盖
   - 项目不存在 → 401
   - 项目存在但 secret 错 → 401
   - 项目 disabled → 202 + ignored
   - payload 缺 project.id → 422
4. **向后兼容**：老数据 `gitlab_base_url` 为空串时，webhook 和 GitLab API 调用应该怎么处理？
   - 方案：如果 `project.gitlab_base_url` 为空，`build_gitlab_client_for_project` 抛出明确错误（ValueError），webhook 返回 400 或记录错误日志
   - 不 fallback 到全局 ENV（因为已经删了）
5. **admin 测试连接接口**：`test_gitlab_webhook_config.py` 里的测试连接逻辑也要用项目级 base_url

---

## 九、验收标准

1. 所有后端 pytest 全部通过
2. 所有前端测试通过
3. 新增 migration 能正常 `upgrade head` 和 `downgrade -1`
4. webhook endpoint：用项目 A 的 secret 调项目 B 的 webhook → 401
5. `generate_release_sql.py` 能正常生成全量 + 增量 SQL 文件
6. .env.example 中不再有 GITLAB_TOKEN / GITLAB_WEBHOOK_SECRET / GITLAB_BASE_URL
