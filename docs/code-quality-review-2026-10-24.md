# 代码设计评审报告（类与方法）

> 日期：2026-10-24 ｜ 范围：`backend/`（128 个 py 文件 / 19.4k 行）+ `frontend/src/`（8.5k 行）
> 目的：按开源项目标准审视「类与方法的组织结构」，只列问题与改造方向，不含实施。
> 依据：AST 度量 + 逐文件阅读（非风格 lint）。所有结论都标了证据位置，可直接点开核对。

---

## 0. 结论

功能完整度确实够了，问题不在"能不能跑"，而在**同一件事被实现两三遍、边界靠约定而非接口撑着**。五个根因：

1. **双管线**：MR 链路是手写脚本（264 行一个函数），commit/push 链路是模板方法（169 行），两条管线做的是同一套步骤，只共享了零散的 `_xxx` 函数。
2. **没有参数对象**：同一组事实（event / review_id / policy / plan / findings / merge / gitlab_client / session_factory / notification / base_url）以 9–18 个关键字参数在 30+ 个函数间手工搬运。
3. **分层是"约定式"的**：引擎层直连 `core.db` 查表、API 层直连 LLM、API 层通过私有符号读进程内缓存、`BaseRepository` 契约被运行时改写绕过。
4. **单例上挂可变状态**：`skipped_files` 在进程级单例引擎上按 review 覆盖，属于已修过的 `_RunState` 同类问题，只是没清干净。
5. **一个文件承担一个域**：`api/admin.py` 2506 行 / 99 函数 / 55 个路由声明，把认证、RBAC、CRUD、序列化、聚合、LLM 调用全放在一起。

好消息：领域模型（events / results 的 frozen dataclass）、仓储层、`usePagedList` 这些"该抽象的地方"抽象得不错，且注释密度高、56 个测试文件在。这些问题属于**结构问题**，不是"从零重写"级别。

---

## 0.1 改造进度

第一批（低风险止血 + 边界）已落地，后端全量测试 580 通过；第二批（管線收敛 / 参数对象）待排期。

| 条目 | 状态 | 落地方式 |
|---|---|---|
| §2.1 引擎单例上的 `skipped_files` | ✅ 已修 | 改为 `ReviewContext.skipped_files` review 局部通道；顺带接进 MR 汇总评论（原来无人消费） |
| §2.3 API 层进程内缓存 | ✅ 已收敛 | 迁至 `services/recent_review_buffer.py`，DB 仍是唯一事实源，缓冲仅降级 |
| §3.3 `api/admin.py` 上帝模块 | ✅ 已拆 | 拆分 `api/admin/` 11 个域模块，66 条路由逐条比对一致（详见 ARCHITECTURE §10.3） |
| §3.4 `BaseRepository` 契约被绕过 | ✅ 已修 | CRUD 辅助改用 `AsyncSession.add/delete/get`，不再运行时改写 `model` |
| §3.3 路由内直连 LLM | ✅ 已修 | 负样本提示词生成下沉 `services/prompt_generation.py` |
| §3.1 双管线 | ⏳ 待做 | 现状与收敛方案见 `docs/orchestration.md` §5 |
| §3.2 长参数表（18/12/10 参） | ⏳ 待做 | 计划引入 `ReviewOutcome` / `FeedbackTarget` 参数对象 |
| §2.2 引擎层直连 DB + 模块级全局缓存 | ⏳ 待做 | 全局提示词 / 负样本改为编排层解析后注入 `ReviewContext` |
| §2.4 无 Unit of Work（11 次 session / 23 处 project 查询） | ⏳ 待做 | 一次审查共享会话，顺带消除重复查询 |
| §2.5 `project_id` 双重语义 | ⏳ 待做 | 区分 `gitlab_project_id: int` 与 `project_uuid: UUID` |
| §3.5 / §3.6 / §3.7 / §3.8 | ⏳ 待做 | LLM 客户端收敛、私有符号契约化、事件接口化、引擎内部分解 |
| §4.1 代码里写 changelog | 🟡 部分 | 新增/改动处已改为描述"为什么"，历史注释待随文件改动自然清理 |
| §4.4 / §4.5 前端 `api.ts` 单模块、页面样板 | ⏳ 待做 | 拆 `api/types` + `api/client` + `api/endpoints`；抽 `<EntityTable>` / `<EntityDialogForm>` |
| §4.6 测试依赖全局状态 | 🟡 部分 | 新增 sqlite 本地跑法（`aiosqlite`），monkeypatch 点随对应模块重构逐步移除 |

---

## 1. 度量基线

| 指标 | 数值 | 位置 |
|---|---|---|
| 最大文件 | 2506 行 / 99 函数 / 55 路由 | `api/admin.py` |
| 最长函数 | 264 行 | `services/review_orchestration/orchestrator.py:126` `review_merge_request` |
| 最大参数表 | 18 个关键字参数 / 178 行 | `core/summary_builder.py:83` `build_review_summary_note` |
| 最多方法类 | 20 方法 / 724 行 | `engines/llm_engine/engine.py:335` `LLMDirectEngine` |
| >80 行函数 | 24 个 | 全后端 |
| >400 行文件 | 27 个 | 全后端 |
| 每次 review 的独立 DB session | ≥11 处 `async with session_factory()` | `services/` |
| 同一 project 的重复查询 | 23 处 `get_by_gitlab_project_id` | `services/`、`engines/` |

---

## 2. P0 — 正确性 / 一致性风险（先处理）

### 2.1 进程级单例引擎上挂 per-review 可变状态

**证据**：`engines/llm_engine/engine.py:372,401,412,446`、`engines/llm_agent/engine.py:305,326` —— `self.skipped_files` 在 `review()` 开头被重置、过程中被 append；而引擎实例由 `EngineRegistry` 全局持有（`engines/registry.py` 文档明确写 "engines are stateless… no per-request mutable state"）。

**本质**：与 2026-09 修掉的 `_observation_cache` 跨 review 污染是同一类 bug，只修了缓存那一半。并发 review 时 A 的 skipped_files 会被 B 清空/混入。

**加重项**：全仓库没有任何消费方读取 `engine.skipped_files`（`core/summary_builder.py` 的 `skipped_files` 参数无人传值）——即这是**既危险又没用的状态**：文档承诺"orchestrator 从 engine.skipped_files 传入"，代码里不存在。

**方向**：把每轮结果收进返回值（如 `EngineOutcome{findings, skipped_files}`），引擎回归纯函数语义；`skipped_files` 要么接通 summary，要么删掉。

### 2.2 引擎层直连数据库 + 模块级全局缓存

**证据**：`engines/llm_engine/engine.py:109 _load_global_prompt` / `:160 _load_negative_prompt` —— 函数内部 `from core.db import AsyncSessionLocal`，直接 `select(GlobalSetting)`，结果写进模块级全局 `_global_prompt_value` / `_negative_prompt_cache`，并额外提供 `_reset_global_prompt_cache()` 给测试用。

**本质**：三层越界同时发生——(a) 引擎（领域策略层）反向依赖持久化层；(b) 绕过 `repositories/`；(c) 全局可变状态 + 测试专用后门。

**影响**：引擎无法脱离数据库单测（现在只能靠 monkeypatch `core.db`）；多实例/多进程下缓存各自为政，前端改全局提示词后最长 60s 才生效且各 worker 不同步。

**方向**：全局提示词/负样本作为 `ReviewContext` 的输入由编排层注入（和 `rules`、`history` 完全同构），引擎只读不查。

### 2.3 API 层进程内缓存 + 私有符号跨模块引用

**证据**：`api/reviews.py:55 _recent_reviews = deque(maxlen=20)`；`api/admin.py:899 from api.reviews import _recent_reviews`；`api/reviews.py:159 clear_recent_reviews_for_tests()`。

**本质**：一个派生数据（最近 20 条审查）既落库又藏内存，作为"列表接口"的数据源之一（`admin.py:887 list_recent_reviews` 在 DB 与 deque 之间分支）。`uvicorn --workers N` 下每个 worker 一份 deque，前端看到的"最近审查"随负载均衡抖动。

**方向**：删掉 deque，统一走 DB 查询（`ReviewRepository.list_recent` 已存在）；如确需缓存，进缓存层而不是模块全局，并提供显式失效。

### 2.4 没有 Unit of Work：一次 review 开 11 个 session、查 5 次 project

**证据**：`services/` 下 11 处独立 `async with session_factory()`（policy / planning / rules / provider / history / engine / persist / notify / lifecycle…）；`get_by_gitlab_project_id` 共 23 处调用，同一次 MR 审查里 project 会被重复查 4–5 次。

**影响**：① 读到的配置可能来自不同快照（审查中途管理员改了规则/策略，前半段用旧、后半段用新）；② 每次多一个连接借还；③ 每个 `_resolve_x` 都要重复"拿 session → 建 repo → 查 project → 判空 → 兜底"的样板。

**方向**：定义 `ReviewUnitOfWork`（一次 review 一个 session/事务、一次性解析 project 及其关联配置），`_resolve_*` 改为接收已解析的聚合对象而非 factory。

### 2.5 `project_id` 有两个含义

**证据**：`services/review_orchestration/events.py:47,93,135` 用 `uuid5("gitlab-project:{id}")` 造一个"伪 UUID"当 `project_id`，而 `ReviewContext.project_id`（`engines/types.py:227`）类型就是 UUID；同时 DB 里 `projects.id` 是另一套 UUID。

**影响**：同一个名字表示两个不同实体，上游拿 `event.project_uuid` 写日志/落库时极易误用；`GitLab project_id`（int）→ 伪 UUID → DB UUID 的三段映射没有任何类型保护。

**方向**：显式区分 `gitlab_project_id: int` / `project_uuid: UUID | None`（未注册即 None），不要用派生 UUID 冒充主键。

---

## 3. P1 — 类与方法设计（核心问题）

### 3.1 双管线：MR 与 commit/push 各写一套同样的流程

**证据**：
- `orchestrator.py:126-389`（264 行，线性脚本）＝ policy → plan → diff → rules/history/provider → engine → blocker → discussions → note → status → persist → notify。
- `handlers/base.py:141-309`（169 行，模板方法）＝ enabled → policy → diff → rules/provider/history → engine → blocker → comments → note → status → persist → notify。

**本质**：`ReviewCommitStyleHandler` 已经把 commit/push 收敛成模板方法（这是项目里最好的一处设计），但 MR 那条路没有复用同一套骨架，于是"同一件事"有两份实现，任何一处改动（比如"engine 失败也要落库"）都得改两遍并写两套测试。

**方向**：抽出 `ReviewPipeline`（步骤编排 + 钩子），MR / commit / push 三个 handler 只提供 `fetch_changes` / `build_context` / `build_note` / `write_back` 差异；orchestrator 退化为"选 handler + 返回结果"的门面。

### 3.2 长参数表代替参数对象

**证据**（同一组事实被搬来搬去）：

| 函数 | 参数数 | 行数 |
|---|---|---|
| `core/summary_builder.py:83 build_review_summary_note` | 18 | 178 |
| `services/.../engine_error.py:121 _handle_engine_error` | 12 | 97 |
| `services/.../persistence.py:36 _persist_review` | 12 | 107 |
| `core/summary_builder.py:360 build_push_review_note` | 11 | 108 |
| `services/.../persistence.py:145 _persist_commit_style_review` | 10 | 107 |
| `services/.../engine_error.py:36 _handle_commit_engine_error` | 10 | 83 |
| `services/.../orchestrator.py:79 __init__` | 10 | 46 |
| `integrations/gitlab/client.py:351 create_merge_request_discussion` | 10 | 67 |

**本质**：调用点长度远超被调用逻辑本身（例如 `orchestrator.py:311-335` 有 25 行只为凑 `build_review_summary_note` 的实参）。18 个参数意味着人无法判断"哪些组合是合法的"，也无法在不改签名的情况下新增一个维度（每次加字段都要改 3 处：定义、调用、测试）。

**方向**：引入 `ReviewOutcome`（review_id / findings 分组 / merge 统计 / plan / blocker / skipped）与 `FeedbackTarget`（gitlab_client / project / sha / note 目标 / detail_url），把"渲染 note"、"落库"、"写回"都变成 `f(outcome, target)`。

### 3.3 `api/admin.py`：一个文件一个"上帝对象"

**证据**：2506 行、99 个函数、55 个路由声明、6 个内联 Pydantic 模型。里面同时存在：
- 认证/RBAC（`_require_admin_auth:154`、`_verify_token_and_load_user:194`、`_require_permission:177`）
- 8 组实体 CRUD（providers / rules / projects / notification channels / user mappings / reviews / findings / engines / users / roles）
- 业务动作（`_recompute_mr_block_status:1283`，100 行；`resolve_finding:1196` 里顺带调 GitLab）
- LLM 调用（`generate_project_negative_prompt:1790`，函数内 `from llm import build_provider` 直接 `chat()`，系统提示词常量写在路由文件里）
- 通用工具（`_paginate:1852`、`_create:1882`、`_apply_sort:1982`、`_generate_rule_slug:2060`）

**本质**：模块按"URL 前缀"切分而不是按"域"切分。任何新功能都往同一个文件里加，导致 ① 冲突热点 ② 无法按域做测试/复用 ③ 依赖图里 API 层同时依赖 models、repositories、llm、core，环环相扣。

**方向**：拆 `api/admin/{auth,providers,rules,projects,reviews,findings,engines,rbac,prompts}.py`；认证依赖下沉 `api/deps.py`；`generate_project_negative_prompt` 的 LLM 调用与提示词搬到 `services/prompt_generation.py`。

### 3.4 `BaseRepository` 的契约被运行时绕过

**证据**：`repositories/base.py:19` 明确要求"子类必须重写 `model` 类属性"，但 `api/admin.py:1894-1896, 1921-1923, 1946-1948` 这样用：

```python
repo: BaseRepository[ModelT] = BaseRepository(db)
repo.model = type(model)          # 运行时改写类属性实例
```

**本质**：泛型基类被当成"带 model 参数的函数"使用，抽象契约在运行时被打破——mypy 看不出问题，`BaseRepository` 又同时暴露 `session` 属性（`base.py:28`）让调用方绕过封装。

**方向**：要么给 `BaseRepository(db, model=Model)` 一个显式构造参数，要么提供 `GenericRepository` 门面；把 `session` 属性收回（现在它被 `_paginate` 之类复用，说明"分页/排序"本身应该是仓储能力）。

### 3.5 LLM 客户端三套并存，且传输层混入 prompt 装配

**证据**：
- `engines/llm_engine/engine.py:226 OpenAICompatibleLLMClient`（把 `build_provider` 每次调用重建；同时负责加载 `system.md`、注入全局提示词、打 request/response 日志）
- `engines/llm_agent/engine.py:119 ProviderAgentClient`（自己做 tools 透传与重试）
- `llm/base.py`（614 行、18 个类）里已有 `OpenAICompatibleProvider` / `AnthropicProvider` / `CustomProvider` 的 `chat` 实现

**本质**：同一件事（把 ProviderConfig 变成一次 SSE/HTTP 对话）有三个入口，命名还都叫"client"。更严重的是 `OpenAICompatibleLLMClient.complete` 承担了"领域策略"（全局提示词注入规则）——它本该只管传输。附带小刺：该方法的 `timeout_seconds` 参数被 `_ = timeout_seconds` 显式丢弃（`engine.py:330`），即接口上有一个不生效的参数。

**方向**：一个 `LLMClient` 抽象（传 provider + messages + tools → 统一 `ChatResponse`），Prompt 装配上移到引擎的 `PromptBuilder`；三个实现收敛成一个。

### 3.6 跨类私有成员调用 / 私有符号构成事实上的公共 API

**证据**：
- `engines/llm_agent/engine.py:372` 直接调 `self._filter_engine._filter_findings(...)`（`LLMDirectEngine` 的私有方法）；comment 里也承认是"组合一个内部实例复用实现"。
- 编排包里所有跨模块共享的函数都是下划线开头：`_persist_review`、`_handle_engine_error`、`_plan_review`、`_resolve_history`、`_post_finding_discussions`、`_extract_int`、`_resolve_engine`…（`orchestrator.py:22-69` 20 行 import 全是私有名）。
- 兼容 shim 反向依赖：`handlers/base.py:393-395` 在方法体里 `from services import review_orchestrator as _legacy_module`，只为让测试的 monkeypatch 生效。

**本质**：包内"公有/私有的语义"失效——下划线名被当 API 用，真正的 API 边界（`ReviewOrchestrator`）反而只是几个私有函数的拼装器。读者无法从命名判断"改动它会波及谁"。

**方向**：给编排包定义明确的对外契约（`ReviewPipeline` / `ReviewOutcome` / 端口接口），其余全部收成模块私有；shim 依赖改为通过构造函数注入 `settings_provider`，删掉 import 兼容层。

### 3.7 模板方法的钩子签名不统一，事件类型靠 duck typing 拼合

**证据**：
- `handlers/base.py` 的 4 个抽象钩子 `_fetch_changes(event)` / `_build_context(event, *, ...)` / `_build_note(*, ...)` / `_head_sha(event)` / `_log_engine_failure(event, name)` —— 有的收 `event`，有的不收，有的全 keyword，没有统一上下文对象。
- `events.py:183 _CommitLikeEvent = GitLabCommitEvent | GitLabPushEvent`，靠 `GitLabPushEvent` 恰好实现了 `commit_sha` / `title` / `web_url` 三个 property 才能被复用（`events.py:135-174` 的自述就是为了"duck-typing 字段对齐"）。
- `GitLabPushEvent.commits: list[dict[str, Any]]`（`events.py:130`）—— 事件里塞未类型化的原始 dict。

**本质**：接口靠"属性名巧合"成立，新增事件类型时只能靠人肉对齐；`Any` 让 commit 字段拼写错误只在运行时炸。

**方向**：定义 `CommitTarget` 协议/基类（`head_sha` / `title` / `web_url`），三个事件显式实现；`commits` 提升为 `CommitInfo` dataclass；钩子统一为 `hook(review_ctx) -> ...`。

### 3.8 引擎类职责过多（一族五个角色）

**证据**：`LLMDirectEngine`（724 行/20 方法）同时做：批处理（`_build_batches`）、prompt 组装（`_build_batch_prompt`/`_base_fixed_values`/`_format_*`）、模板渲染、行数重定位、结果解析（`_parse_findings`/`_normalise_raw_finding`）、二次过滤（`_filter_findings` 125 行）、健康检查。`LLMAgentEngine`（517 行/13 方法）同时做：客户端选择、prompt 组装、循环控制（`_run_agent_loop` 134 行）、工具调度（`_execute_tool_calls` 120 行）、预算记账、观察压缩、trace 上报、降级决策。

**本质**：`ReviewEngine` 只有一个方法 `review()`，于是所有职责只能堆在私有方法里；`review()` 与 `_run_agent_loop()` 之间靠 `assert ctx.provider is not None` 传递前置条件（`llm_agent/engine.py:422-423`），违反"构造函数/契约保证不变量"的原则，关掉 assert 就跑飞。

**方向**：按"PromptBuilder / BatchPlanner / FindingParser / FilterStage / AgentLoop"拆成可独立测试的协作者类，引擎只做编排；前置条件移到入口校验并返回明确错误。

---

## 4. P2 — 可维护性

### 4.1 代码里写 changelog，注释解释"是哪个 PR 干的"
`"PR2 of the orchestration split"`（`handlers/base.py:1`）、`"PR3"`/`"PR4"`（`persistence.py:5,8`）、`"feat/rescan-changed-files"`、`"feat/per-file-concurrent-review"`、`"Issue #35 → PR #52"`（`CHANGELOG.md` 之外的代码注释里）。这些信息属于 git history / CHANGELOG，留在 docstring 里会随时间腐烂（读者不知道 PR2 是什么）。

### 4.2 类型被弱化到 `Any` / `dict[str, Any]`
`api/admin.py:288 Page.items: list[Any]`（分页响应对 OpenAPI 完全无类型信息，前端只能手写类型）；`_verify_token_and_load_user` 返回 `dict[str, Any]` 且 `user` 可能为 `None`（3 处重复的 legacy 分支返回同一形状，`admin.py:220/231/250`），下游只能靠 `state.current_user` 猜；`notification_service` 全程 `review_data: dict[str, Any]`。

### 4.3 死代码 / 互斥的兼容路径
`services/review_orchestrator.py` 33 行 DEPRECATED shim（还导出私有函数 `_is_line_number_valid_for_current_diff`）；`__init__` 里 `session_factory`/`notification_service` 为 None 的"MVP 兼容路径"（`orchestrator.py:103-109`）在线上永不发生，却让每个下游函数多一层判空；`CommitReviewResult` docstring 写"不落库"而实现已落库（`results.py:31`）。

### 4.4 前端 `api.ts` 单模块
1419 行、143 个 export：类型定义（~400 行）+ localStorage 5 个 key 的读写（~130 行）+ 请求封装 + 20+ 实体的 CRUD 函数全在一个文件。任何后端字段变动都往同一文件里改，且类型与请求实现无法分治。建议拆 `api/types/*`、`api/auth-storage.ts`、`api/client.ts`、`api/endpoints/*`。

### 4.5 前端页面级样板重复
`UsersPage` / `RolesPage` 各自实现 `XxxDialogForm`（open 时回填、`submitting`、`errorMessage`、`handleSubmit`），`UserMappingsPage`（369 行）是 11 个列表页里唯一没用 `usePagedList` 的——同一套分页/筛选/弹窗流程存在两份写法。列表分页 hook 覆盖率不错（10/11），值得再往前做成 `<EntityTable>` + `<EntityDialogForm>` 两级抽象。

### 4.6 测试依赖全局状态，缺少契约/并发测试
56 个测试文件，但 16 个用 `monkeypatch` 打全局（`get_settings`、`core.db.AsyncSessionLocal`、`_recent_reviews`、legacy shim），这正是 §2.2/§2.3/§3.6 的直接后果。缺的测试类型：引擎并发隔离（`skipped_files` 类问题无回归）、仓储契约（跨 PG/MySQL）、管线各阶段顺序契约。

---

## 5. 建议顺序（不建议一次全改）

| 批次 | 内容 | 理由 |
|---|---|---|
| 第 1 批（低风险、止血） | §2.1 `skipped_files`、§2.3 `_recent_reviews`、§4.3 删兼容路径 | 只删不建，改动面小，消除并发/多 worker 不一致 |
| 第 2 批（边界） | §2.2 引擎去 DB、§3.5 LLM 客户端收敛、§3.4 仓储契约 | 都在"层间接口"上，改完引擎可脱离 DB 单测 |
| 第 3 批（结构） | §3.2 参数对象 → §3.1 统一管线 → §3.3 拆 admin | 参数对象是统一管线的前置；admin 拆分可与前端 api.ts 拆分并行 |
| 第 4 批（质量） | §3.8 引擎内部分解、§4.1/4.2 注释与类型、§4.4/4.5 前端拆分 | 收益偏长期，放在核心结构稳定后 |

一个可选的"对外门面"目标态（供讨论，非本次实施）：

```
api/*.py            只做 HTTP：鉴权、校验、调用 application service
application/        ReviewRunner（选 handler）· ConfigResolver（UnitOfWork）· PromptGeneration
domain/             Plan / Outcome / Policy / Finding（纯数据，无 IO）
engines/            ReviewEngine = 纯函数（输入 ctx → 返回 outcome）
ports/              GitLabPort · LLMPort · RepoPort（引擎/应用层只依赖这些）
infrastructure/     gitlab client · llm providers · repositories · db
```

---

## 附录：检查方法

1. AST 度量脚本：文件 LOC、类方法数、函数行数、参数个数、类规模（本报告第 1 节全部数值）。
2. 结构相似度检测：对 ≥18 行长函数做 token 级 `SequenceMatcher`（阈值 0.85）——全后端无"逐字复制"（说明作者做过抽取），但存在**结构性重复**（同一流程两份实现，见 §3.1）。
3. 人工通读：`services/review_orchestration/*`、`engines/llm_{engine,agent}/*`、`api/{admin,stats,reviews,gitlab_webhook}.py`、`repositories/*`、`core/{config,summary_builder,block_policy}.py`、前端 `api.ts` + 4 个代表页。
