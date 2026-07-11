# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **前端管理后台（8 个页面 Linear 化）**：Providers / Projects / Rules / ReviewRecords / Findings / FalsePositives / Engines / NegativeExamples 全部按 Linear 风格重构，抽出 DataRow / StatusRow 通用组件（Issue #35 → PR #52）。
- **orchestrator provider 注入**：修复 MVP 遗留缺口 —— `review_merge_request` 构造 `ReviewContext` 时未从 DB 读 Project 关联的 Provider，导致 `llm-direct` 引擎永远 skip。现在按 GitLab project_id → Project → Provider 链路解析并注入解密后的 `ProviderConfig`；Project/Provider 缺失或禁用时保持向后兼容（Issue #67 → PR #68）。
- **commit_sha 去重**：orchestrator 在拿 changes 前先按 `(project_id, commit_sha)` 查一次 DB，命中已完成评审（done / engine_error）直接复用旧结果，不再重跑引擎、不再重复写 GitLab（Issue #65）。
- **误报评审 UI 补齐**：Rules 页新增编辑/删除按钮，Projects 页新增「AI 供应商」下拉；后端补 `updateRule` / `deleteRule` API 与 `test_admin_errors.py` 三分支单测（Issue #54 → PR #55）。
- **数据存储层跨方言支持**：10 个 model 从 PG-only 改为 `sa.Uuid` / `sa.JSON` 通用类型；UUID 主键改 Python 层 `uuid.uuid4` 生成；`docker-compose` 新增 `mysql:8.0` profile；Alembic 迁移重写为跨方言写法（Issue #56 Part A → PR #57）。
- **CI 双库矩阵**：GitHub Actions 后端测试同时跑 PostgreSQL 15 与 MySQL 8.0（`backend-test-postgres` + `backend-test-mysql`）；补充 `docs/storage.md` 说明双库切换（Issue #56 Part B → PR #58）。
- **Repository 层解耦**：新增 `backend/app/repositories/`（`BaseRepository[Model]` 泛型基类 + 10 个具体仓储），将数据访问从 `admin.py` 三个内部 helper 抽出；路由端点签名与错误映射（IntegrityError→409 / SQLAlchemyError→500）零变更（Issue #56 Part C → PR #59）。
- **Fernet 密钥启动 fail-fast**：`validate_secret_key` 在应用启动时校验 `SECRET_KEY` 合法性，避免运行时才暴露；`_commit_or_400` 明确区分 IntegrityError 与其它 SQLAlchemyError 错误映射。
- **Dockerfile 使用清华镜像源**：backend/frontend 分别切换 pypi.tuna.tsinghua.edu.cn / npmmirror.com，pip install 从 410s 降到 9.3s（PR #53）。

### Changed

- 后端启动依赖新增 `aiomysql` / `pymysql`（可选，仅当 `DATABASE_URL` 指向 MySQL 时生效）。
- 数据访问统一走 Repository 层，未来注入 mock repository / 切换存储后端只需实现同一接口。

## [0.1.0] - TBD

MVP 首版本（Phase 1）。面向 GitLab + Jenkins 内网试运行，覆盖 MR 自动评审、行级 Discussion 写回、按分支分级阻断、误报反馈闭环与项目级配置。

### Added

- **后端骨架与数据模型**：FastAPI + SQLAlchemy(async) + PostgreSQL + Alembic + Redis；10 张表（providers / rules / projects / project_rules / project_block_policies / engines / reviews / review_findings / negative_examples / audit_logs），敏感字段（api_key、access_token、webhook_secret）Fernet 加密落库，响应统一脱敏 `****`。
- **ReviewEngine 抽象 + LLMEngine 实现**：可插拔引擎注册表（`@register_engine` 装饰器，单例 `EngineRegistry`）；内置 `llm-direct` 引擎走 OpenAI 兼容 chat-completions，5 段式 Prompt（scope / rules / false-positive history / diff / output contract）+ JSON 契约解析 + 误报历史过滤 + 行号回退定位。
- **可插拔 LLM 供应商**：OpenAI 兼容（DeepSeek / Qwen / GLM / Kimi / Moonshot / 火山方舟 / Ollama / vLLM）、Anthropic 原生、Custom 自定义三种协议。
- **GitLab 客户端**：拉取 MR diff / changes、写行级 Discussion、写 Note、设置 commit status；统一 `GitLabClientError`。
- **Webhook 接收 + 评审编排**：`POST /api/webhooks/gitlab` 接收 MR Hook（`X-Gitlab-Token` 校验），`ReviewOrchestrator` 串联 diff 过滤 → 引擎执行 → 阻断计算 → GitLab 反馈（行级 discussion + 摘要 note + commit status）。
- **阻断策略匹配引擎**：按目标分支 glob 匹配策略（`master`/`release/*`/`hotfix/*` → BLOCKER，`*` → NONE），支持 `NONE`/`INFO`/`WARNING`/`BLOCKER`/`ENGINE_ERROR_ONLY` 五档阈值与 `block_on_engine_error`；引擎异常时确定性降级。
- **REST API（JWT 认证）**：providers / rules / projects（含嵌套 rules + block_policies）/ reviews / findings / engines/configs 六套 CRUD + 分页/排序/过滤；误报闭环（标记 PENDING → confirm 写入 negative_examples / reject）；negative-examples 列表。标准 JWT（HS256）签发与校验。
- **Jenkins 同步评审**：`POST /api/reviews`（`X-Internal-Token`）同步返回 `has_blocker` / `finding_count` / `blocker_count` / `policy_applied` / `review_url`，供 Pipeline 决定是否失败；`GET /api/reviews/recent` 供 MVP Dashboard。
- **Jenkins Pipeline 模板**：Declarative Pipeline 与 Freestyle Job 接入示例（见 `jenkins/` 与 [docs/jenkins-setup.md](docs/jenkins-setup.md)）。
- **部署与配置**：Docker Compose 一键启动（postgres / redis / backend / frontend）；`scripts/start-mvp.sh` 自动生成 `.env` 与随机密钥；后端启动自动 `alembic upgrade head` + `seed.py`（默认引擎 / 阻断策略模板 / 基础规则）。
- **文档**：[docs/setup.md](docs/setup.md)、[docs/gitlab-setup.md](docs/gitlab-setup.md)、[docs/jenkins-setup.md](docs/jenkins-setup.md)、[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)；本期新增 [docs/api.md](docs/api.md)（REST API 完整文档）、[docs/rule-authoring.md](docs/rule-authoring.md)（规则编写指南）、[docs/troubleshooting.md](docs/troubleshooting.md)（常见问题排查）。
- **测试**：后端 pytest + pytest-asyncio + respx（mock GitLab HTTP），覆盖 GitLab 客户端、评审编排、阻断策略、diff 过滤、LLM 引擎、误报闭环、admin CRUD、JWT 认证；本期新增端到端测试 `backend/tests/e2e/test_full_flow.py`（respx mock 全链路：阻断分支命中 / 非阻断分支 / 引擎超时降级 / 重复 commit 触发）。94 个测试通过，覆盖率 88%。

### Changed

- 认证改为标准 JWT（`/api/auth/login` 签发，`Authorization: Bearer`），取代早期自定义 token 方案；Jenkins 内部触发与 GitLab Webhook 仍分别使用 `X-Internal-Token` 与 `X-Gitlab-Token`。
- 项目 API 的 `rules` / `block_policies` 改为嵌套整体替换语义（PATCH 传则整体替换）。

### Known Issues

- **无 RAG 知识库**：评审为 diff-only + 规则 prompt，未接入 pgvector / 检索增强；RAG 计划在 v0.3.0+ 落地。
- **单账号管理**：MVP 仅支持单一管理员账号（`ADMIN_USERNAME` / `ADMIN_PASSWORD`），无多用户 / 角色 / SSO / 审计日志。
- **编排器运行时注入未完成**：`ReviewOrchestrator` 当前未把 DB 中的 provider / project_rules / block_policies 注入 `ReviewContext`，`llm-direct` 引擎在未注入 provider 时安全降级为「不产出 finding」；评审 finding 暂不落库（`/api/findings` 仅返回管理 API 创建的记录），通过 GitLab discussion 与 `POST /api/reviews` 响应摘要体现。
- **无 commit_sha 去重**：相同 commit 重复触发会重新评审，暂无跳过 / 结果缓存。
- **同步评审**：`POST /api/reviews` 同步执行，大 MR 可能占用 Web worker 较久；生产化需队列化。
- **生产化未就绪**：缺 HTTPS 终止、鉴权网关、IP allowlist、多副本、备份恢复与监控告警；当前定位为内网试运行。

### Dependencies

后端（Python 3.11，见 `backend/pyproject.toml`）：

- fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg, alembic
- pydantic, pydantic-settings, redis, cryptography, httpx, tiktoken, python-multipart, PyJWT

后端开发依赖：pytest, pytest-asyncio, pytest-cov, httpx, ruff, mypy, types-redis, respx

前端（见 `frontend/package.json`）：React 19, Vite, TypeScript, Vitest, @testing-library/react

基础设施：PostgreSQL 15, Redis 7, Docker Compose v2, Nginx（前端静态托管 + 反向代理）

[Unreleased]: https://github.com/geXingW/ai-code-reviewer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/geXingW/ai-code-reviewer/releases/tag/v0.1.0
