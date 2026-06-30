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

- **后端**：Python 3.11 + FastAPI + SQLAlchemy + PostgreSQL + Alembic + Celery + Redis
- **前端**：Vue 3 + Vite + Element Plus
- **部署**：Docker Compose 一键启动

## 路线图

### v0.1.0 MVP（Phase 1，预计 3 周）

- [x] 项目治理基础设施
- [ ] 后端骨架 + 数据模型
- [ ] ReviewEngine 抽象 + LLMEngine 实现
- [ ] GitLab 客户端（diff / discussion / commit status）
- [ ] Webhook 接收 + 评审编排
- [ ] 阻断策略匹配引擎
- [ ] 前端管理后台（7 个页面）
- [ ] Jenkins Pipeline 模板
- [ ] 部署 + 配置文档

### v0.2.0（Phase 2）

- [ ] OcrEngine 适配器（接入阿里 ocr CLI）
- [ ] 误报评审管理（CONFIRMED → negative_examples）
- [ ] 团队级看板（误报率、规则触发率、模型耗时对比）

### v0.3.0+（Phase 3 远期）

- [ ] RAG 知识库（pgvector）
- [ ] AgentEngine（Claude Code 深度评审）
- [ ] Skill 包格式（替代扁平 Rule）
- [ ] 用户权限 / SSO / 审计日志

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## Quick Start

> ⚠️ MVP 开发中，以下命令在 v0.1.0 发布后可用。当前阶段请关注 Issues。

```bash
# 1. clone + 配置
git clone https://github.com/geXingW/ai-code-reviewer.git
cd ai-code-reviewer
cp .env.example .env  # 编辑配置

# 2. 一键启动
docker compose up -d

# 3. 访问后台
open http://localhost:5173
```

详细部署指南：

- [docs/setup.md](docs/setup.md) — 部署
- [docs/gitlab-setup.md](docs/gitlab-setup.md) — GitLab Webhook 配置
- [docs/jenkins-setup.md](docs/jenkins-setup.md) — Jenkins Pipeline 集成

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
