# 项目级 Commit 审查配置开关

## 背景
commit 审查（GitLab Push Hook 逐 commit 审查）后端代码已完整，但：
1. 当前通过全局 ENV `COMMIT_REVIEW_ENABLED` 控制，没有前端 UI
2. 用户需要按项目独立控制 commit 审查开关，而非全局开/关

## 变更范围

### 1. 后端改动

**1a. DB 迁移 (Alembic)**
- 新增迁移 `0012_project_commit_review`，在 `projects` 表加两个字段：
  - `commit_review_enabled` BOOLEAN NOT NULL DEFAULT FALSE（默认关闭，安全第一）
  - `commit_review_max_per_push` INTEGER NOT NULL DEFAULT 10

**1b. ORM Model (`backend/models/project.py`)**
- `Project` 类新增两个 mapped_column：
  ```python
  commit_review_enabled: Mapped[bool] = mapped_column(
      Boolean, default=False, server_default=false(), nullable=False,
  )
  commit_review_max_per_push: Mapped[int] = mapped_column(
      Integer, default=10, nullable=False,
  )
  ```

**1c. Pydantic Schema (`backend/schemas/project.py`)**
- `ProjectCreate` 新增：
  ```python
  commit_review_enabled: bool = False
  commit_review_max_per_push: int = Field(default=10, ge=1, le=20)
  ```
- `ProjectUpdate` 新增：
  ```python
  commit_review_enabled: bool | None = None
  commit_review_max_per_push: int | None = None
  ```
- `ProjectRead` 新增（从 attributes 读取）：
  ```python
  commit_review_enabled: bool
  commit_review_max_per_push: int
  ```

**1d. Webhook 逻辑 (`backend/api/gitlab_webhook.py`)**
- `_handle_push_hook` 中，开关判断改为：
  ```python
  # 原来：全局 settings.commit_review_enabled
  # 改为：project.commit_review_enabled
  if not project.commit_review_enabled:
      return GitLabWebhookResponse(processed=False, reason="commit_review_disabled")
  ```
- `max_per_push` 改为 `project.commit_review_max_per_push`（兜底 `settings.commit_review_max_per_push`）

**1e. Orchestrator (`backend/services/review_orchestrator.py`)**
- `review_commit_event` 方法中，同样的开关判断改为用 `project.commit_review_enabled`

**1f. 幂等跳过逻辑**
- `_handle_push_hook` 中 dispatch 每个 commit 前的幂等检查（`find_completed_commit_review`）保持不变

### 2. 前端改动

**2a. TypeScript 类型 (`frontend/src/api.ts`)**
- `ProjectConfig` 新增：
  ```typescript
  commit_review_enabled: boolean;
  commit_review_max_per_push: number;
  ```
- `ProjectFormPayload` 新增：
  ```typescript
  commit_review_enabled: boolean;
  commit_review_max_per_push: number;
  ```
- `ProjectUpdatePayload` 新增：
  ```typescript
  commit_review_enabled?: boolean;
  commit_review_max_per_push?: number;
  ```

**2b. ProjectDialog 表单 (`frontend/src/components/dialogs/ProjectDialog.tsx`)**
- `initialEmptyForm` 新增默认值：
  ```typescript
  commit_review_enabled: false,
  commit_review_max_per_push: 10,
  ```
- 编辑模式回填时读取 `initialData.commit_review_enabled` 和 `initialData.commit_review_max_per_push`
- 在「审查配置」FieldGroup 中新增一个子卡片：
  - 标题：「Commit 推送审查」
  - 内容：checkbox「启用 commit 推送审查」+ 数字输入「单次推送最多审查 commit 数 (1-20)」
  - 样式：复用底部「启用项目」的 `flex items-center justify-between rounded-lg border` 卡片风格
  - 当 `commit_review_enabled=false` 时，数字输入置灰 disabled

**2c. App.tsx 同步**
- 检查 `initialProjectForm` 和项目列表渲染是否需要更新（列表页不需要展示 commit 审查状态，这只是配置项）

### 3. 测试更新

**3a. 后端测试**
- `test_gitlab_webhook.py` `test_push_hook_disabled_returns_commit_review_disabled`：确保 Project 记录 `commit_review_enabled=False` 时返回正确
- `test_commit_review.py`：确保 Project 记录 `commit_review_enabled=True` 时审查正常触发
- 新增测试：`commit_review_max_per_push` 使用 project 级别值而非全局 settings

**3b. 前端测试**
- `App.test.tsx` 和 `ProjectDialog` 测试（如有）：确保新字段正确渲染和提交

### 4. 迁移脚本处理旧数据
- 迁移 `0012` 的 `upgrade` 中，`commit_review_enabled` 默认 `FALSE`，`commit_review_max_per_push` 默认 `10`
- 不做旧数据回填（全局 `COMMIT_REVIEW_ENABLED=true` 的旧行为不自动迁移到项目级，避免误开启）

## 设计原则
- **默认关闭**：新项目/旧项目默认 `commit_review_enabled=false`，主动配置才开启
- **项目级隔离**：全局 ENV `COMMIT_REVIEW_ENABLED` 保留作为紧急止血开关（`settings.commit_review_enabled` 在 webhook 层优先级最高：`if not settings.commit_review_enabled` → 直接拒绝，不看 project 字段），但默认不再依赖它
- **向后兼容**：`handle_push_hook` 中 `settings.commit_review_enabled` 作为第一道防线保留，project 级开关作为第二道防线

## 执行顺序
1. 后端：DB 迁移 → Model → Schema → Webhook/Orchestrator 逻辑修改 → 测试
2. 前端：类型 → ProjectDialog 表单 → App.tsx 同步 → 测试
3. 验证：`ruff check` + `mypy app` + `npm run build` + `npm test` 全部通过