# 架构设计

> 本文档固化 ai-code-reviewer 的核心架构决策。所有 contributor 在动手前应先阅读。
>
> 文档更新规则：架构层面的变更必须先改本文档，再写代码。

## 一、定位与目标

**一句话定位**：基于 GitLab + Jenkins 的 AI Code Review Web 服务，作为合并前的质量门禁。

**核心目标**：

1. MR 合并前 AI 自动评审 → 行级评论 + 可阻断流水线
2. 按项目独立配置：评审引擎、模型供应商、规则集、阻断策略
3. 误报反馈闭环：开发者标记 → 管理员审定 → 自动入 prompt 负样本

**非目标**（明确不做）：

- 不替代人工 Code Review，只做辅助
- 不做 IDE 插件（专注 MR 阶段）
- 不做代码自动修复（只评论 + 建议）

## 二、整体架构

```
开发者 push + 开 MR
       ↓
   GitLab Webhook ──────────┐
       ↓                   │
   Jenkins Pipeline         │ (双触发，独立处理)
       │                   ↓
       │            ai-code-reviewer 后端
       │              ┌─────────────────┐
       │              │ Webhook Receiver│  ← 接 Note Hook（误报标记）
       │              └─────────────────┘
       │
       ↓ (在 stage AI Review 中)
   curl POST /api/reviews
       ↓
   ┌──────────────────────────────────────┐
   │  ai-code-reviewer 后端                │
   │                                       │
   │  Orchestrator                         │
   │    ├─ load project config             │
   │    ├─ match block policy by branch    │
   │    ├─ fetch diff (GitLab API)         │
   │    ├─ engine.review(ctx) → findings   │
   │    ├─ write discussions + summary     │
   │    └─ compute has_blocker             │
   │                                       │
   │  Engine Registry                      │
   │    ├─ LLMEngine (MVP)                 │
   │    ├─ OcrEngine (Phase 2)             │
   │    └─ AgentEngine (Phase 3)           │
   │                                       │
   │  LLM Provider Abstraction             │
   │    ├─ OpenAICompatible                │
   │    ├─ Anthropic                       │
   │    └─ Custom                          │
   └──────────────────────────────────────┘
       ↓
   返回 {has_blocker, finding_count, ...}
       ↓
   Jenkins stage 决定 exit code
       ↓
   GitLab Commit Status (Jenkins 自动回写)
       ↓
   MR 页面 Merge 按钮启用/禁用
```

## 三、关键抽象：ReviewEngine

整个架构的核心抽象，所有评审引擎实现此接口。

```python
# backend/app/engines/base.py

class ReviewEngine(ABC):
    @abstractmethod
    def name(self) -> str:
        """引擎标识，如 'llm-direct' / 'alibaba-ocr'"""

    @abstractmethod
    def review(self, ctx: ReviewContext) -> List[Finding]:
        """核心方法：输入评审上下文，返回 finding 列表"""

    @abstractmethod
    def supports_feedback(self) -> bool:
        """是否支持误报反馈学习"""

    @abstractmethod
    def health_check(self) -> dict:
        """健康检查"""
```

**为什么这么抽象**：

- 解耦评审能力与平台集成（webhook / 阻断 / 后台 / 反馈闭环跟引擎无关）
- 留口子接外部引擎（OCR CLI、Agent CLI 等）
- 业务能力（引擎 A 试不行换 B）

**Phase 1 必交付**：`ReviewEngine` 接口 + `EngineRegistry` + `LLMEngine` 实现。

## 四、LLMEngine 内部设计

采用业界主流的 5 段式 Prompt 设计：

```
1. plan_task          — 规划：哪些文件评、按什么顺序
2. main_task          — 主审：按规则集生成 findings
3. review_filter_task — 二次过滤：让 LLM 自审，丢掉低置信度
4. re_location_task   — 行号校准：用 existing_code 滑动窗口定位
5. memory_compression — 上下文压缩（大 PR 时用）
```

**MVP 仅实现 main + filter 两段**，re_location 用代码层做（不调 LLM），plan / compress 进 Phase 2。

**关键工程细节**：

- Finding 必须含 `existing_code` 字段（LLM 输出原代码片段）
- 后端代码用滑动窗口在 diff 里匹配定位行号 → 解决行号漂移
- 强制 JSON 输出（response_format 或 prompt 末尾强约束）
- 单次 LLM 调用超时 60s，整体评审超时 300s（可配）

## 五、数据模型

```
projects                      // 项目
├─ id
├─ name
├─ gitlab_project_id
├─ gitlab_access_token (加密)
├─ webhook_secret
├─ engine_id          → engines (FK)
├─ provider_id        → providers (FK)
├─ enabled
├─ timeout_seconds
├─ max_files
├─ ignore_paths       JSONB
└─ default_block_severity

providers                     // LLM 供应商池
├─ id
├─ name             "火山方舟-ark-code-latest"
├─ protocol         openai_compatible / anthropic / custom
├─ base_url
├─ api_key (加密)
├─ model
├─ temperature
├─ max_tokens
├─ extra_headers    JSONB
└─ enabled

engines                       // 引擎池
├─ id
├─ name             "llm-direct" / "llm-agent" / "alibaba-ocr"
├─ type             builtin / external_cli
├─ config           JSONB
└─ enabled

rules                         // 规则库（跨项目）
├─ id
├─ rule_id          "java.npe-check"
├─ title
├─ prompt_snippet
├─ severity_default INFO / WARNING / BLOCKER
├─ languages        JSONB ["java", "kotlin"]
├─ path_patterns    JSONB ["**/*.java"]
├─ enabled
└─ grace_period_until  // 新规则宽限期

project_rules                 // 项目 ↔ 规则 多对多
├─ project_id
├─ rule_id
├─ enabled
└─ severity_override

project_block_policies        // ⭐ 阻断策略
├─ id
├─ project_id
├─ branch_pattern   "master" / "release/*"
├─ block_severity   NONE / INFO / WARNING / BLOCKER / ENGINE_ERROR_ONLY
├─ block_on_engine_error  bool
├─ require_all_resolved   bool
└─ priority         数字越小越先匹配

reviews                       // 评审记录
├─ id
├─ project_id
├─ mr_iid
├─ source_branch
├─ target_branch
├─ commit_sha
├─ status           pending / running / done / failed
├─ engine_used
├─ provider_used
├─ policy_applied
├─ has_blocker
├─ finding_count
├─ duration_ms
└─ raw_llm_output   // 调试用

findings                      // 评审发现
├─ id
├─ review_id
├─ file_path
├─ line_number
├─ rule_id
├─ severity
├─ title
├─ description
├─ suggestion
├─ existing_code     // 用于定位
├─ confidence
├─ gitlab_discussion_id
├─ fp_status         NONE / PENDING / CONFIRMED / REJECTED
├─ fp_marked_by
├─ fp_marked_at
├─ fp_marked_reason
├─ fp_reviewed_by
├─ fp_reviewed_at
└─ fp_review_note

negative_examples             // 误报负样本（CONFIRMED 后入库）
├─ id
├─ rule_id
├─ project_id        // NULL = 全局
├─ code_snippet
├─ explanation
├─ source_finding_id
├─ approved_by
└─ approved_at
```

## 六、阻断策略匹配引擎

```python
def match_block_policy(
    policies: List[ProjectBlockPolicy],
    target_branch: str,
) -> ProjectBlockPolicy:
    """按 priority 顺序找第一个匹配的 branch_pattern"""
    sorted_policies = sorted(policies, key=lambda p: p.priority)
    for p in sorted_policies:
        if fnmatch(target_branch, p.branch_pattern):
            return p
    return DEFAULT_POLICY  # 兜底：BLOCKER
```

**默认策略模板**（新项目自动创建）：

- priority=1, `master` → `BLOCKER`
- priority=2, `release/*` → `BLOCKER`
- priority=3, `hotfix/*` → `BLOCKER`
- priority=99, `*`（兜底）→ `NONE`

**has_blocker 计算**：

```python
severity_rank = {"INFO": 1, "WARNING": 2, "BLOCKER": 3}
threshold = severity_rank.get(policy.block_severity, 999)  # NONE → 999
has_blocker = any(severity_rank[f.severity] >= threshold for f in findings)
```

## 七、误报反馈闭环

```
1. 开发者在 GitLab MR 页面 resolve 某条 Discussion
       ↓
2. Note Webhook 触发 → POST /webhooks/gitlab/note
       ↓
3. 后端识别为 "resolved discussion" → 更新 finding.fp_status = PENDING
       (要求开发者 resolve 时附带 /false-positive: <reason> 标记)
       ↓
4. 管理员在 "误报评审队列" 页面看到 PENDING 列表
       ↓
5. 管理员审定:
   ├─ Approve → fp_status = CONFIRMED → 自动写 negative_examples
   └─ Reject  → fp_status = REJECTED  → 标记为误标记
       ↓
6. 下次评审，prompt 自动追加该规则的 negative_examples
       (按 rule_id + project_id 检索最近 N 条)
```

**关键设计**：

- 误报反馈是"半自动"，不是开发者一标记就生效（避免 prompt 被污染）
- 管理员审定有审计日志（fp_reviewed_by/at/note）
- negative_examples 按 `(rule_id, project_id)` 范围，支持全局 + 项目级
- LLMEngine 才支持反馈学习；OcrEngine 跳过（supports_feedback=False）

## 八、Jenkins 集成

Pipeline 中加一段 stage（详见 [docs/jenkins-setup.md](jenkins-setup.md)）：

```groovy
stage('AI Code Review') {
    when { changeRequest() }
    steps {
        script {
            def response = sh(
                script: 'curl ... POST /api/reviews ...',
                returnStdout: true
            )
            def result = readJSON(text: response)
            if (result.has_blocker) {
                error "AI Review 阻断: ${result.blocker_count} 个问题"
            }
        }
    }
}
```

阻断机制依赖：

- Jenkins GitLab Plugin 自动写 GitLab Commit Status
- GitLab 项目设置勾选 "Pipelines must succeed"
- (可选) 勾选 "All threads must be resolved"

## 九、关键工程取舍

| 取舍点 | 选择 | 理由 |
|---|---|---|
| 同步 / 异步 | MVP 同步，Phase 2 加 Celery | 同步够用且简单；超 30s 评审才需要异步 |
| 数据库 | PostgreSQL（不用 SQLite） | SQLite 并发写入锁粒度大，生产环境体验差 |
| 行号定位 | 滑动窗口（不依赖 LLM 给行号） | 业界成熟方案，根治行号漂移问题 |
| 多次 push | 全量评审 | MVP 简单优先；增量评审复杂度高 |
| 误报反馈 | 半自动（管理员审定） | 避免 prompt 被污染 |
| 阻断默认 | master/release → BLOCKER, 其他 NONE | 渐进引入，让团队先用起来 |
| 评审引擎 | 抽象 + 多实现（`llm-direct` 纯 diff 单轮 / `llm-agent` 多轮工具调用，可调查提交影响范围） | 不被 LLM API 涨价 / 上游 CLI 改接口卡脖子 |

## 十、代码分层与模块边界

> 本节是「代码往哪里放」的唯一约定。新增功能先在这里找到自己的层，再动手。

### 10.1 目录地图（后端）

```
backend/
├── main.py                 应用工厂（create_app）+ 路由装配
├── app.py / __main__.py    启动脚本（加载 .env + uvicorn）
├── api/                    HTTP 层：只做鉴权、校验、序列化、错误映射
│   ├── admin/              管理后台（按域拆包，见 10.3）
│   ├── reviews.py          Jenkins 同步审查入口
│   ├── gitlab_webhook.py   Webhook 接收与事件归一化
│   ├── stats.py            统计聚合
│   ├── engines.py          运行时引擎信息 / 健康检查
│   └── health.py           健康探针
├── services/               应用层：编排、通知、提示词生成、仓库读取
│   ├── review_orchestration/  审查编排（见 docs/orchestration.md）
│   ├── prompt_generation.py   负样本提示词生成（LLM 调用在此，不在路由里）
│   ├── notification_service.py 钉钉/Webhook 通知渲染与投递
│   ├── recent_review_buffer.py 首页"最近审查"的进程内降级缓冲
│   └── repo_reader.py      GitLab 只读仓库访问器（实现引擎侧 RepoReader 协议）
├── engines/                引擎层：ReviewContext → findings（无 IO 副作用之外的东西）
│   ├── base.py             ReviewEngine 抽象
│   ├── registry.py         进程级单例注册表（引擎必须无状态）
│   ├── types.py            ReviewContext / Finding / RuleSpec / SkippedFile …
│   ├── llm_engine/         纯 diff 五段式 Prompt 引擎
│   └── llm_agent/          多轮工具调用引擎（含轨迹落库）
├── llm/                    LLM 供应商适配（openai_compatible / anthropic / custom）
├── repositories/           数据访问层：一模型一仓储，只做查询与写库
├── models/                 SQLAlchemy ORM（含 Fernet 加密 Mixin）
├── schemas/                Pydantic v2 出入参（时区处理在 _datetime.py）
└── core/                   横切能力：config / db / block_policy / summary_builder …
```

### 10.2 依赖方向（硬约束）

```
api  →  services  →  repositories  →  models
 ↓                     ↓
schemas              engines（只依赖 engines.types，不碰 db / models）
 ↓
core（config / db / policy / summary_builder：任何层可用，但不反向依赖上面三层）
```

四条不可违反的规则：

1. **api 不写业务**。路由里不出现 LLM 调用、跨表事务、复杂计算；这些下沉到 `services/`。历史上
   `POST /api/projects/{id}/negative-prompt/generate` 曾在路由里直接 `build_provider().chat()`，
   现已迁到 `services/prompt_generation.py`。
2. **engine 不碰数据库**。引擎只能读 `ReviewContext`（含 `provider` / `rules` / `history` /
   `repo_reader`），不得 `from core.db import ...`。之前 `llm_engine` 直连 `global_settings`
   读全局提示词属于越界，须由编排层解析后注入。
3. **engine 实例必须无状态**。`EngineRegistry` 持有的是进程级单例，任何 per-review 可变数据
   都放在 review 局部：要么是方法内局部变量，要么是 `ReviewContext` 上的回填字段
   （例：`ReviewContext.skipped_files`）。历史事故：去重缓存与 `skipped_files` 挂在实例上，
   并发 review 会互相污染。
4. **repository 只做数据访问**，不返回 HTTP 语义、不拼展示文案；`schemas/` 负责序列化，
   `core/summary_builder.py` 负责展示文案。

### 10.3 管理后台 API 分包

`api/admin.py` 曾是 2500 行单文件（99 个函数 / 55 个路由）。现按资源域拆为包：

| 模块 | 负责 |
|---|---|
| `api/admin/deps.py` | Bearer 鉴权、RBAC 权限守卫、JWT 签发 |
| `api/admin/common.py` | `Page` 响应包装、白名单排序、CRUD 薄封装、错误映射 |
| `api/admin/auth.py` | `/auth/login`、`/auth/me` |
| `api/admin/providers.py` | 模型供应商配置 |
| `api/admin/rules.py` | 规则库 |
| `api/admin/projects.py` | GitLab 项目 + 通知渠道 + 项目成员映射 |
| `api/admin/reviews.py` | 审查记录（MR/Push/Commit）、最近审查面板、Agent 轨迹 |
| `api/admin/findings.py` | 问题列表、误报队列、负样本库、阻断状态重算 |
| `api/admin/engines.py` | 引擎持久化配置 |
| `api/admin/prompts.py` | 全局提示词、项目级负样本提示词 |
| `api/admin/rbac.py` | 用户、项目授权、角色与权限矩阵 |

约定：每个域模块自己声明 `APIRouter(prefix="/api", dependencies=[Depends(_require_admin_auth)])`，
路由级权限用 `dependencies=[Depends(_require_permission("page:xxx"))]` 显式声明；
`api/admin/__init__.py` 只做聚合，不写业务。

### 10.4 两条审查管线的现状

| 链路 | 落点 | 形态 |
|---|---|---|
| MR（`review_merge_request`） | `services/review_orchestration/orchestrator.py` | 线性编排，含增量合并 / reuse / 生命周期事件 |
| commit / push | `services/review_orchestration/handlers/base.py` | 模板方法：公共管线在基类，子类只注入 diff 获取与 note 渲染 |

两条链路的**持久化、通知、引擎解析、策略匹配**已经共享同一批函数；剩下的差异（diff 语义、
汇总评论模板、跳过条件）是真实差异。后续把 MR 也收敛进模板方法骨架（见
`docs/orchestration.md` §5「演进方向」）。

## 十一、文档索引

- [setup.md](setup.md) — 部署
- [gitlab-setup.md](gitlab-setup.md) — GitLab 配置（webhook / token / 项目设置）
- [jenkins-setup.md](jenkins-setup.md) — Jenkins Pipeline 集成
- [engine-development.md](engine-development.md) — 如何开发新引擎（接口契约、注册、测试）
- [rule-authoring.md](rule-authoring.md) — 规则编写指南
- [orchestration.md](orchestration.md) — 审查编排链路（阶段、数据结构、错误路径）
- [commenting-guide.md](commenting-guide.md) — 注释规范（docstring 与行内注释的分工、必写场景）
- [storage.md](storage.md) — 数据模型与存储约定
- [review-status-flow.md](review-status-flow.md) — 审查状态流转
- [api.md](api.md) — REST API 文档
- [code-quality-review-2026-10-24.md](code-quality-review-2026-10-24.md) — 代码质量评审与改造进度
