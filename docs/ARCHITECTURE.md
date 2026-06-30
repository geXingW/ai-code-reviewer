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
- 留口子接外部引擎（阿里 ocr CLI、Claude Code 等）
- 业务能力（引擎 A 试不行换 B）

**Phase 1 必交付**：`ReviewEngine` 接口 + `EngineRegistry` + `LLMEngine` 实现。

## 四、LLMEngine 内部设计

借鉴 alibaba/open-code-review 的 5 段式 Prompt：

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
├─ name             "llm-direct" / "alibaba-ocr"
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

## 九、业界对比与设计取舍

参考分析见 Issue 中附的两份调研报告。本项目相对两个同类项目的差异：

**对比 sunmh207/AI-Codereview-Gitlab**：

- 我们额外做：阻断 / 行级 Discussion / 误报反馈 / 规则可配置
- 借鉴：统一 webhook 端点 + GitLab API 调用细节（changes API 延迟重试）

**对比 alibaba/open-code-review**：

- 我们形态：Web 服务（vs 他们 CLI）
- 借鉴：Provider 注册表、5 段式 Prompt、path_rule_map、code_comment 滑动窗口定位

## 十、关键工程取舍

| 取舍点 | 选择 | 理由 |
|---|---|---|
| 同步 / 异步 | MVP 同步，Phase 2 加 Celery | 同步够用且简单；超 30s 评审才需要异步 |
| 数据库 | PostgreSQL（不用 SQLite） | sunmh207 SQLite 并发锁死的教训 |
| 行号定位 | 滑动窗口（不依赖 LLM 给行号） | 阿里 ocr 验证有效，根治行号漂移 |
| 多次 push | 全量评审 | MVP 简单优先；增量评审复杂度高 |
| 误报反馈 | 半自动（管理员审定） | 避免 prompt 被污染 |
| 阻断默认 | master/release → BLOCKER, 其他 NONE | 渐进引入，让团队先用起来 |
| 评审引擎 | 抽象 + 多实现 | 不被 LLM API 涨价 / 上游 CLI 改接口卡脖子 |

## 十一、文档索引

- [setup.md](setup.md) — 部署
- [gitlab-setup.md](gitlab-setup.md) — GitLab 配置（webhook / token / 项目设置）
- [jenkins-setup.md](jenkins-setup.md) — Jenkins Pipeline 集成
- [engine-development.md](engine-development.md) — 如何开发新引擎
- [rule-authoring.md](rule-authoring.md) — 规则编写指南
- [api.md](api.md) — REST API 文档
