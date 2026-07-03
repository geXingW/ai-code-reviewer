# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- 项目治理基础设施初始化（LICENSE / README / CI / Issue & PR 模板 / 架构文档 / 贡献指南 / 编辑器配置）
- 双引擎架构设计文档（ReviewEngine 抽象 + LLMEngine + OcrEngine 路线）
- 13 个 MVP 阶段 Issue 任务拆解（v0.1.0 Milestone）

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

- **前端实际为 React 而非 Vue 3**：`README.md` 技术栈段落误标为 Vue 3 + Element Plus + Pinia；实际前端为 React 19 + Vite + TypeScript（见 `frontend/package.json`、[docs/setup.md](docs/setup.md)）。文档描述待统一修正。
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
