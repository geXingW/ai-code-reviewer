# ai-code-reviewer

> AI 驱动的代码评审系统，专为 **GitLab + Jenkins** 流水线设计 — 可插拔 LLM 供应商、可配置规则、多维度阻断策略、误报反馈闭环。

[![CI](https://github.com/geXingW/ai-code-reviewer/actions/workflows/ci.yml/badge.svg)](https://github.com/geXingW/ai-code-reviewer/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

---

## 项目定位

在 MR 合并到目标分支**之前**，由 AI 对代码 diff 做评审：

- ✅ 发现问题 → 按文件/行号写回 GitLab 的行级 Discussion
- ✅ 命中阻断级问题 → 通过 Jenkins Pipeline 阻断 MR 合并
- ✅ 开发者可标记误报 → 进入评审队列 → 管理员确认后入 prompt 负样本库 → 模型逐步自我增强
- ✅ 按项目独立配置：评审引擎、模型供应商、规则集、阻断策略

## 核心特性

### 双引擎架构（可扩展）

通过 `ReviewEngine` 抽象层支持多种评审引擎：

- **LLMEngine**（MVP 自带）：直连 LLM API，5 段式 Prompt（plan / main / filter / re-locate / compress）+ 滑动窗口行号定位 + 二次过滤
- **OcrEngine**（Phase 2）：适配阿里 [open-code-review](https://github.com/alibaba/open-code-review) CLI，企业级评审质量
- **AgentEngine**（Phase 3）：适配 Claude Code 等 Agent CLI，深度代码库探索

### 可插拔 LLM 供应商

- OpenAI 兼容协议（DeepSeek、Qwen、GLM、Kimi、Moonshot、火山方舟、Ollama、vLLM 等）
- Anthropic 原生协议（Claude 系列）
- Custom 自定义协议（私有部署）

### 项目级配置

每个 GitLab 项目独立配置：

- 评审引擎（LLM 直连 / 阿里 ocr）
- 模型供应商 + 模型名
- 启用的规则集（跨项目共享，按需勾选）
- **阻断策略**（按目标分支分级，详见下文）

### 阻断策略（按目标分支分级）

**默认推荐**：

- `master` / `release/*` → `BLOCKER` 阻断
- 其他分支 → `NONE` 只评论不阻断

**完整档位**：`NONE` / `INFO` / `WARNING` / `BLOCKER` / `ENGINE_ERROR_ONLY`

### 误报反馈闭环

```
开发者标记误报
    ↓
进入 PENDING 队列
    ↓
管理员定期评审
    ↓
CONFIRMED 写入 negative_examples
    ↓
下次评审 prompt 自动追加负样本
    ↓
模型逐步自我增强
```

## 技术栈

- **后端**：Python 3.11 + FastAPI + SQLAlchemy (async) + Alembic + Redis
- **数据库**：PostgreSQL 15 或 MySQL 8.0（跨方言，二选一）
- **前端**：React 19 + Vite + TypeScript + Vitest
- **部署**：Docker Compose 一键启动

## 路线图

### v0.1.0 MVP（Phase 1）

- [x] 项目治理基础设施
- [x] 后端骨架 + 数据模型（10 表 + Fernet 加密 + Alembic）
- [x] ReviewEngine 抽象 + LLMEngine 实现
- [x] GitLab 客户端（diff / discussion / commit status）
- [x] Webhook 接收 + 评审编排
- [x] 阻断策略匹配引擎
- [x] REST API（5 套 CRUD + 误报闭环 + JWT 认证）
- [x] Jenkins Pipeline 模板
- [x] 部署 + 配置文档
- [x] 前端管理后台（8 个页面，Linear 风格）

### v0.2.0（Phase 2）

- [ ] OcrEngine 适配器（接入阿里 ocr CLI）
- [x] 误报评审管理（CONFIRMED → negative_examples）
- [x] 数据存储层跨方言（PostgreSQL + MySQL 8.0）
- [x] Repository 层解耦（数据访问抽象）
- [ ] 团队级看板（误报率、规则触发率、模型耗时对比）

### v0.3.0+（Phase 3 远期）

- [ ] RAG 知识库（pgvector）
- [ ] AgentEngine（Claude Code 深度评审）
- [ ] Skill 包格式（替代扁平 Rule）
- [ ] 用户权限 / SSO / 审计日志

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## Quick Start

> ⚠️ 当前为 MVP 内网试运行版本，可以用于 GitLab + Jenkins 小范围联调；生产化部署还需要补齐鉴权、网关、审计和高可用能力。

```bash
# 1. clone + 配置
git clone https://github.com/geXingW/ai-code-reviewer.git
cd ai-code-reviewer
cp .env.example .env  # 编辑配置

# 2. 一键启动
./scripts/start-mvp.sh

# 3. 访问后台
open http://localhost:5173
```

详细部署指南：

- [docs/setup.md](docs/setup.md) — 部署
- [docs/gitlab-setup.md](docs/gitlab-setup.md) — GitLab Webhook 配置
- [docs/jenkins-setup.md](docs/jenkins-setup.md) — Jenkins Pipeline 集成

## REST API

所有管理 API 需通过 JWT Bearer Token 认证。启动后访问 `http://localhost:8000/docs` 查看交互式 OpenAPI 文档。

### 认证

```bash
# 登录获取 JWT
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}'

# 响应: {"access_token": "eyJhbGci...", "token_type": "bearer", "expires_in": 86400}

# 携带 token 调用管理 API
curl http://localhost:8000/api/providers \
  -H "Authorization: Bearer eyJhbGci..."
```

### 端点总览

**管理认证**
- `POST /api/auth/login` — 登录，返回标准 JWT

**LLM 供应商管理**
- `GET /api/providers` — 列表（支持 `limit` / `offset` / `sort` / `q` / `enabled`）
- `POST /api/providers` — 创建（api_key 自动 Fernet 加密，响应脱敏 `****`）
- `GET /api/providers/{id}` — 详情
- `PATCH /api/providers/{id}` — 更新
- `DELETE /api/providers/{id}` — 删除

**规则库管理**
- `GET /api/rules` — 列表
- `POST /api/rules` — 创建
- `GET /api/rules/{id}` — 详情
- `PATCH /api/rules/{id}` — 更新
- `DELETE /api/rules/{id}` — 删除

**项目管理（含嵌套 rules + block_policies）**
- `GET /api/projects` — 列表
- `POST /api/projects` — 创建（可同时传 `rules` + `block_policies` 嵌套数组）
- `GET /api/projects/{id}` — 详情（含嵌套 rules + policies）
- `PATCH /api/projects/{id}` — 更新（传 `rules` / `block_policies` 会整体替换）
- `DELETE /api/projects/{id}` — 删除

**评审记录**
- `GET /api/reviews` — 列表（支持 `project_id` / `status` / `mr_iid`）
- `GET /api/reviews/{id}` — 详情

**评审发现**
- `GET /api/findings` — 列表（支持 `review_id` / `severity` / `fp_status` / `file_path`）
- `GET /api/findings/{id}` — 详情

**误报闭环**
- `POST /api/findings/{id}/false-positive` — 开发者标记误报（→ PENDING）
- `GET /api/false-positives/pending` — 管理员查看待评审队列
- `POST /api/false-positives/{id}/confirm` — 确认误报 → 写入 `negative_examples` 表
- `POST /api/false-positives/{id}/reject` — 驳回误报
- `GET /api/negative-examples` — 负样本库列表

**引擎管理**
- `GET /api/engines` — 运行时引擎列表 + 健康探测
- `GET /api/engines/{name}/health` — 单引擎健康详情
- `GET /api/engines/configs` — 持久化引擎配置列表
- `POST /api/engines/configs` — 创建引擎配置
- `GET /api/engines/configs/{id}` — 详情
- `PATCH /api/engines/configs/{id}` — 更新（含启用/禁用）
- `DELETE /api/engines/configs/{id}` — 删除

**Jenkins 同步评审**
- `POST /api/reviews` — 同步触发评审（`X-Internal-Token` 认证，Jenkins Pipeline 调用）
- `GET /api/reviews/recent` — 最近 20 条评审摘要（MVP Dashboard）

**GitLab Webhook**
- `POST /webhooks/gitlab` — 接收 GitLab MR Hook（`X-Gitlab-Token` 签名校验）

### 列表端点通用参数

所有 `GET` 列表端点支持：

- `limit` — 每页条数（1-100，默认 20）
- `offset` — 偏移量（默认 0）
- `sort` — 排序字段，`-` 前缀降序（如 `-created_at`）
- `q` — 关键词模糊搜索
- `enabled` — 按启用状态筛选

### 误报闭环示例

```bash
# 1. 开发者标记误报
curl -X POST http://localhost:8000/api/findings/{id}/false-positive \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"marked_by": "dev@example.com", "reason": "Generated file"}'

# 2. 管理员查看待评审队列
curl http://localhost:8000/api/false-positives/pending \
  -H "Authorization: Bearer $TOKEN"

# 3. 确认误报 → 自动写入 negative_examples
curl -X POST http://localhost:8000/api/false-positives/{id}/confirm \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reviewed_by": "lead@example.com", "note": "Known safe wrapper"}'
```

### 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `DATABASE_URL` | PostgreSQL 异步连接串 | `postgresql+asyncpg://...` |
| `REDIS_URL` | Redis 连接串 | `redis://localhost:6379/0` |
| `SECRET_KEY` | Fernet 加密密钥（加密 api_key / token 等敏感字段） | 需修改 |
| `INTERNAL_API_TOKEN` | 服务间调用令牌（Jenkins / Webhook 内部触发） | `test-internal-token` |
| `ADMIN_USERNAME` | 管理后台登录用户名 | `admin` |
| `ADMIN_PASSWORD` | 管理后台登录密码 | `admin` |
| `JWT_SECRET` | JWT 签名密钥（≥ 32 字节） | 需修改 |
| `JWT_ALGORITHM` | JWT 签名算法 | `HS256` |
| `JWT_EXPIRES_IN` | JWT 有效期（秒） | `86400`（24h） |
| `GITLAB_BASE_URL` | GitLab 实例地址 | `http://localhost` |
| `GITLAB_TOKEN` | GitLab API Token | 需配置 |
| `GITLAB_WEBHOOK_SECRET` | Webhook 签名校验密钥 | `test-webhook-secret` |
| `DEFAULT_REVIEW_ENGINE` | 默认评审引擎 | `llm-direct` |
| `CORS_ORIGINS` | 允许的跨域来源 | `["http://localhost:5173"]` |

---

## 与同类项目对比

**对比 [sunmh207/AI-Codereview-Gitlab](https://github.com/sunmh207/AI-Codereview-Gitlab)**：

- ✅ 我们：阻断合并 / 行级 Discussion / 误报反馈 / 规则可配置
- ❌ 他们：不阻断 / 只整体评论 / 无反馈 / 规则写死

**对比 [alibaba/open-code-review](https://github.com/alibaba/open-code-review)**：

- ✅ 我们：Web 服务形态 / 阻断 / 后台管理 / 误报闭环 / 可作为 ocr 上层平台
- ⚙️ 他们：CLI 形态 / 不阻断 / 无后台 / 评审引擎质量高（我们 Phase 2 适配它）

详见 [docs/ARCHITECTURE.md#业界对比](docs/ARCHITECTURE.md)。

## 贡献

欢迎贡献！请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

[Apache License 2.0](LICENSE)
