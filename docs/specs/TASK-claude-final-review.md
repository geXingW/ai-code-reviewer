# 任务书：项目级 GitLab 凭证改造 - 收尾与质量保证

## 背景
当前分支 `feat/project-level-gitlab-credentials` 已完成主要编码工作，
需要你做一次完整的收尾：代码审查、跑通测试、修复发现的问题、确保交付质量。

## 工作目录
`/home/ubuntu/ai-code-reviewer`

## 分支
`feat/project-level-gitlab-credentials`（已基于 master 创建）

## 已完成的改动清单
1. Project 模型新增 `gitlab_base_url` 字段（明文 String(512)）
2. Alembic migration 0007
3. Project schema (Create/Update/Read) 新增 `gitlab_base_url`
4. webhook 入口改为项目级校验（查 project → 校验 secret → 构造 client）
5. 新增 `services/gitlab_client_factory.py` 工厂函数
6. admin.py 改用工厂函数构造 GitLabClient
7. config.py 移除 `gitlab_base_url`/`gitlab_token`/`gitlab_webhook_secret`
8. `.env.example` 同步清理
9. 前端 `api.ts` + `ProjectDialog.tsx` 新增 `gitlab_base_url` 字段
10. 测试文件已更新（conftest 新增 db_session_factory/db_client fixtures）
11. 新增 `scripts/generate_release_sql.py` 生成全量+增量 SQL
12. release workflow 和 Dockerfile 集成 SQL 生成
13. README.md 和 docs/setup.md 文档更新
14. spec 文档在 `docs/specs/2026-08-08-project-level-gitlab-credentials.md`

## 你的任务

### 1. 完整代码审查
通读所有改动文件，检查：
- 逻辑正确性：webhook 的 secret 校验流程是否正确，加密字段读取是否正确
- 类型安全：mypy strict 模式下是否通过
- 代码风格：ruff 检查是否通过
- 边界处理：项目不存在、secret 为空、base_url 为空、项目 disabled 等错误路径
- 一致性：所有构造 GitLabClient 的地方是否都用了工厂函数
- 前端：表单验证、初始值、编辑回填是否正确

### 2. 跑测试并修复
后端 pytest 需要 PostgreSQL。本地没有 PG，你有两个选择：
- 方案 A：用 SQLite 内存模式跑（需要确认 SQLAlchemy 模型是否兼容 SQLite，
  如果有 PG/MySQL 特定类型，可能需要调整测试配置）
- 方案 B：安装并启动一个本地 PG（apt install postgresql 或其他方式）

选择最稳妥的方案，确保所有测试通过。必须覆盖的测试文件：
- `tests/test_gitlab_webhook.py` — webhook 项目级校验的核心测试
- `tests/test_gitlab_webhook_config.py` — admin 里的 webhook 配置测试
- `tests/test_api_crud.py` — 项目 CRUD 测试
- 其他现有测试确保没有回归

**重点关注错误路径**：
- 项目不存在 → 401（不泄露项目存在性）
- 项目存在但 secret 错 → 401
- 项目 disabled → 202 + processed=false
- payload 缺 project.id → 422
- 项目 gitlab_base_url 为空 → 合理的错误处理

### 3. 验证 generate_release_sql.py
确保脚本能正常生成全量+增量 SQL 文件。

### 4. 前端检查
- 前端 build 能否通过（`npm run build` 在 frontend 目录）
- 前端 lint 检查
- ProjectDialog 的 `gitlab_base_url` 字段是否正确集成

### 5. 提交前检查
- `git diff` 确认没有意外改动
- 所有新文件都有适当的 docstring / 注释
- 改动符合 spec 要求

## 约束
- 不要修改其他不相关的文件
- 测试失败时，先理解根因再修复，不要为了绿而 hack
- 保持现有代码风格一致
- 完成后所有改动都 commit 到当前分支
- 返回：改动摘要、测试结果、发现的问题及修复情况
