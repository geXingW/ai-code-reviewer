# Contributing to ai-code-reviewer

感谢你愿意为本项目贡献！本文档说明开发流程、分支规范、提交规范、Review 流程。

## 行为准则

请先阅读 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 开发环境

### 前置依赖

- Python 3.11+
- Node.js 20+
- Docker + Docker Compose
- PostgreSQL 15+（开发可用 docker compose 起）
- Redis 7+（开发可用 docker compose 起）

### 本地启动

```bash
git clone https://github.com/geXingW/ai-code-reviewer.git
cd ai-code-reviewer

# 启动依赖服务（postgres + redis）
docker compose up -d postgres redis

# 后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload

# 前端
cd ../frontend
npm install
npm run dev
```

## 分支命名

```
feat/<short-desc>       新功能
fix/<short-desc>        Bug 修复
docs/<short-desc>       文档
chore/<short-desc>      工程化、依赖、CI
refactor/<short-desc>   重构（不改行为）
test/<short-desc>       仅测试
perf/<short-desc>       性能优化
```

例：`feat/llm-engine-prompt-filter`、`fix/gitlab-diff-retry`

## 提交规范（Conventional Commits）

```
<type>(<scope>): <subject>

[body]

[footer]
```

**type**：`feat` / `fix` / `docs` / `style` / `refactor` / `perf` / `test` / `chore` / `build` / `ci`

**示例**：

```
feat(engines): introduce ReviewEngine abstraction with Registry

- Define abstract base class with review/health_check/supports_feedback
- Add EngineRegistry for runtime engine discovery
- Stub LLMEngine + OcrEngine placeholder

Closes #4
```

## PR 流程

1. 从 `master` 拉新分支
2. 提交代码（遵循上述规范）
3. push 后开 PR，**填好 PR 模板**
4. CI 必须全绿
5. 至少 1 个 Reviewer Approve
6. **行级 Discussion 全部 resolved**
7. Squash Merge 进 master

## 测试要求

- 新功能 → 必须有单测
- Bug 修复 → 必须有回归测试
- 后端覆盖率目标：≥ 70%（核心 engines / orchestrator 模块 ≥ 85%）

```bash
# 后端测试
cd backend && pytest --cov

# 前端测试
cd frontend && npm test
```

## 文档

- 改动 API → 更新 [docs/api.md](docs/api.md)
- 改动数据模型 → 更新 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) + 新建 Alembic migration
- 新增 engine → 在 [docs/engine-development.md](docs/engine-development.md) 加示例
- 用户可见行为变更 → 更新 [CHANGELOG.md](CHANGELOG.md) `[Unreleased]` 段

## 安全

发现安全漏洞请按 [SECURITY.md](SECURITY.md) 私下报告，**不要**开公开 Issue。
