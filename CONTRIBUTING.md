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

### 本地启动

```bash
git clone https://github.com/geXingW/ai-code-reviewer.git
cd ai-code-reviewer

# 启动依赖服务（postgres）
docker compose up -d postgres

# 后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
# 空库时手动执行建库 SQL（应用启动不自动建表）
python scripts/export_schema_sql.py && mysql -u<user> -p <database> < sql/schema-mysql.sql
python app.py --reload

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
# 后端测试（需要 PostgreSQL / MySQL）
cd backend && pytest --cov

# 没有数据库时：用 SQLite 跑同一套用例（需 pip install -r requirements-dev.txt）
cd backend && DATABASE_URL=sqlite+aiosqlite:///./_pytest.db pytest

# 前端测试
cd frontend && npm test
```

> 后端测试通过 `DATABASE_URL` 决定测试库，`tests/conftest.py` 每个用例重建 schema。
> `--workers` / 生产库连接串**不要**用于测试环境。

## 代码结构约定

改动前请先读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第十节「代码分层与模块边界」。四条硬约束：

1. **api 不写业务**：路由只做鉴权 / 校验 / 序列化 / 错误映射，业务逻辑放 `services/`。
2. **engine 不碰数据库**：引擎只消费 `ReviewContext`，配置由编排层解析后注入。
3. **engine 实例必须无状态**：`EngineRegistry` 里是进程级单例，per-review 数据放局部变量或
   `ReviewContext` 的回填字段（`skipped_files`）。
4. **repository 只做数据访问**：不返回 HTTP 语义、不拼展示文案。

新增管理后台接口 → 加到 `api/admin/` 对应的域模块（而不是再造一个大文件）；
新增评审引擎 → 按 [docs/engine-development.md](docs/engine-development.md) 的清单实现。

## 注释要求

注释规范见 [docs/commenting-guide.md](docs/commenting-guide.md)。一句话：**docstring 写契约，
行内注释写"为什么"**——业务阈值、顺序/并发约束、外部系统怪癖、降级决策、魔法数字必须解释。

提交前可自查（输出只是线索，不是门禁）：

```bash
cd backend
python scripts/comment_audit.py --min-lines 30
```

## 文档

- 改动 API → 更新 [docs/api.md](docs/api.md)
- 改动数据模型 → 更新 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) + 运行 `python scripts/export_schema_sql.py` 重新生成建库 SQL
- 改动审查流程（阶段 / 失败语义 / 落库） → 更新 [docs/orchestration.md](docs/orchestration.md)
- 新增 engine → 按 [docs/engine-development.md](docs/engine-development.md) 实现并补示例
- 用户可见行为变更 → 更新 [CHANGELOG.md](CHANGELOG.md) `[Unreleased]` 段

## 安全

发现安全漏洞请按 [SECURITY.md](SECURITY.md) 私下报告，**不要**开公开 Issue。
