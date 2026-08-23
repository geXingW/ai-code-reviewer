# Review 与 Finding 状态流转全景

> 本文档梳理 `reviews` 表（Review）和 `review_findings` 表（Finding）的**所有状态值**、**产生条件**、**流转路径**，以及 `lifecycle_event` 记账 Review 的作用与隐患。

---

## 1. Review.status — 审查执行状态

`Review.status` 列定义在 [models/review.py](../backend/models/review.py#L40-L45)，`String(30)`，`NOT NULL`，DB 层 `server_default='pending'`。

Schema 层 `ReviewStatus` Literal（[schemas/review.py](../backend/schemas/review.py#L13)）：

```python
ReviewStatus = Literal["pending", "running", "done", "failed", "engine_error"]
```

### 1.1 各状态含义与产生条件

| status | 含义 | 谁写入 | 产生条件 |
|--------|------|--------|----------|
| `pending` | 审查已创建但尚未完成 | Model `default`/`server_default` | **当前 orchestrator 不会主动写入此值**。它是 DB 层的默认值，只有在"创建 Review 行时未显式设 status"时才会出现。orchestrator 的 `_persist_review` 创建行时直接传 `status_value="done"` 或 `"engine_error"`，绕过了 pending。 |
| `running` | 审查进行中 | — | **Schema 预留值，当前代码无任何写入路径**。存在于 Literal 和回归测试 `_ORCHESTRATOR_WRITTEN_STATUSES` 中，是防御性声明，实际 DB 中不会出现。 |
| `done` | 审查正常完成 | orchestrator `_persist_review` | MR 审查（open/update/reopen）engine 正常返回后，落库时 `status_value="done"`（[review_orchestrator.py#L505](../backend/services/review_orchestrator.py#L505)）。lifecycle 记账 review 也用 `done`（[review_orchestrator.py#L2080](../backend/services/review_orchestrator.py#L2080)）。 |
| `engine_error` | 引擎执行异常 | orchestrator `_handle_engine_error` → `_persist_review` | MR 审查时 `engine.review()` 抛异常，走降级路径落库 `status_value="engine_error"`（[review_orchestrator.py#L1291](../backend/services/review_orchestrator.py#L1291)）。 |
| `failed` | 审查失败（泛义） | — | **Schema 预留值，orchestrator 不写入**。仅出现在测试种子数据中（[test_stats_api.py#L139](../backend/tests/test_stats_api.py#L139)）。与 GitLab commit status 的 `failed` state 是不同概念。 |

### 1.2 实际写入路径

```
MR 审查（open / update / reopen）
  ├─ engine 正常 → _persist_review(status_value="done")
  └─ engine 异常 → _handle_engine_error → _persist_review(status_value="engine_error")

MR 生命周期（close / merge）
  └─ _handle_lifecycle_event → ReviewRow(status="done", lifecycle_event="mr_closed"|"mr_merged")

commit / push 审查
  └─ 不落库（_persist_review 不被调用），CommitReviewResult 仅在内存返回
```

> **结论**：当前 DB 中 `reviews.status` 只会出现 `done` 和 `engine_error` 两个值。`pending` 是 schema 默认值但被 orchestrator 绕过；`running` 和 `failed` 是预留值。

---

## 2. Review.lifecycle_event — MR 生命周期记账标记

`Review.lifecycle_event` 列定义在 [models/review.py](../backend/models/review.py#L89-L92)，`String(20)`，`nullable=True`。

Schema 层 `ReviewLifecycleEvent` Literal（[schemas/review.py](../backend/schemas/review.py#L27)）：

```python
ReviewLifecycleEvent = Literal["mr_closed", "mr_merged"]
```

### 2.1 各值含义与产生条件

| lifecycle_event | 含义 | 产生条件 | finding 联动 |
|-----------------|------|----------|-------------|
| `NULL` | 普通审查（默认） | MR open / update / reopen 触发的常规审查 | finding 的 `status` 由 engine 输出 + 增量合并逻辑决定 |
| `mr_closed` | MR 关闭事件记账 | GitLab webhook `action="close"` → `_handle_mr_closed` → `_handle_lifecycle_event(terminal_status="mr_closed")` | 该 MR 所有 `open` finding → `status='mr_closed'`，`resolved_in_review_id` 指向此记账 review |
| `mr_merged` | MR 合并事件记账 | GitLab webhook `action="merge"` → `_handle_mr_merged` → `_handle_lifecycle_event(terminal_status="resolved")` | 该 MR 所有 `open` finding → `status='resolved'`，`resolved_in_review_id` 指向此记账 review |

### 2.2 lifecycle 记账 Review 的特征

`_handle_lifecycle_event`（[review_orchestrator.py#L2053](../backend/services/review_orchestrator.py#L2053)）创建的记账行有固定特征：

```python
ReviewRow(
    status="done",
    engine_used=None,
    has_blocker=False,      # ← 恒为 False
    finding_count=0,        # ← 恒为 0（不关联任何 finding）
    duration_ms=0,
    review_mode="full",
    lifecycle_event="mr_closed" | "mr_merged",
    parent_review_id=None,
)
```

**关键点**：记账行不跑 engine、不调 GitLab changes/note/commit_status API、不关联 finding。它纯粹是一条时间线记录，用于：
- 保留"MR 在此时刻被关闭/合并"的审计痕迹
- 作为 finding 的 `resolved_in_review_id` 外键目标
- 前端据此渲染"MR 已关闭"/"MR 已合并"徽章

### 2.3 何时会产生 lifecycle 状态

```
GitLab MR webhook action:
  ├─ "open"    → 常规审查（lifecycle_event=NULL）
  ├─ "reopen"  → 翻转 finding + 常规审查（lifecycle_event=NULL）
  ├─ "update"  → 常规审查（lifecycle_event=NULL）
  ├─ "close"   → lifecycle 记账（lifecycle_event="mr_closed"）← 产生 lifecycle
  └─ "merge"   → lifecycle 记账（lifecycle_event="mr_merged"）← 产生 lifecycle
```

**只有 `close` 和 `merge` 两个 webhook action 会产生 lifecycle 记账行。**

---

## 3. Finding.status — 问题状态

`Finding.status` 列定义在 [models/finding.py](../backend/models/finding.py#L75-L80)，`String(20)`，`NOT NULL`，`server_default='open'`。

### 3.1 各状态值与产生条件

| status | 含义 | 谁写入 | 产生条件 |
|--------|------|--------|----------|
| `open` | 问题活跃（未修复） | orchestrator `_persist_review` / `reopen_mr_closed` | 1. engine 新产出的 finding 入库时 `status="open"`；2. MR reopen 时 `mr_closed` → `open`（`reopen_mr_closed`） |
| `resolved` | 问题已修复 | orchestrator `mark_resolved` | 1. 增量审查中改动文件的老 finding 被标记 resolved；2. MR merge 时所有 open finding → resolved |
| `mr_closed` | MR 已关闭，问题随 MR 归档 | `mark_mr_closed` | MR close 事件触发，该 MR 所有 open finding → mr_closed |

### 3.2 Finding 状态流转图

```
                    ┌──────────────────────────────────────────────┐
                    │                                              │
                    ▼                                              │
              ┌─── open ──────────────────────────┐                │
              │      │  │  │                      │                │
              │      │  │  │ MR close             │ MR merge        │
              │      │  │  ▼                      ▼                 │
              │      │  │  mr_closed           resolved             │
              │      │  │  │                      │                 │
              │      │  │  │ MR reopen            │                 │
              │      │  │  ▼                      │                 │
              │      │  └─ open ◄─────────────────┘                 │
              │      │                                              │
              │      │ 增量审查：改动文件的老 finding                 │
              │      ▼                                              │
              │   resolved                                           │
              │                                                      │
              └─── (finding 生命周期随 review 行 CASCADE 删除) ───────┘
```

详细路径：

| 起点 | 终点 | 触发条件 | 代码位置 |
|------|------|----------|----------|
| — | `open` | engine 新产出 finding 入库 | `_persist_review` → `FindingRow(status="open")` |
| `open` | `mr_closed` | MR close 事件 | `mark_mr_closed`（[review.py#L224](../backend/repositories/review.py#L224)） |
| `mr_closed` | `open` | MR reopen 事件 | `reopen_mr_closed`（[review.py#L261](../backend/repositories/review.py#L261)） |
| `open` | `resolved` | MR merge 事件 | `mark_resolved`（[review.py#L200](../backend/repositories/review.py#L200)） |
| `open` | `resolved` | 增量审查中改动文件的老 finding | `_persist_review` → `mark_resolved`（[review_orchestrator.py#L2316](../backend/services/review_orchestrator.py#L2316)） |

### 3.3 Finding.fp_status — 误报状态（独立维度）

`Finding.fp_status` 列定义在 [models/finding.py](../backend/models/finding.py#L49-L54)，`server_default='NONE'`。

| fp_status | 含义 | 产生条件 |
|-----------|------|----------|
| `NONE` | 默认，未标记误报 | 初始值 |
| `PENDING` | 待审核的误报候选 | 管理员手动标记（`POST /api/false-positives/{id}/mark`） |
| `CONFIRMED` | 确认为误报 | 管理员审核通过（`POST /api/false-positives/{id}/confirm`） |
| `REJECTED` | 拒绝误报标记 | 管理员审核拒绝（`POST /api/false-positives/{id}/reject`） |

误报确认后会触发 `_recompute_mr_block_status` 重算 MR 阻断状态。

---

## 4. Review.review_mode — 审查模式

`Review.review_mode` 列定义在 [models/review.py](../backend/models/review.py#L77-L82)，`server_default='full'`。

| review_mode | 含义 | 何时写入 DB |
|------------|------|------------|
| `full` | 全量审查（base..head 完整 diff） | 首次审查 / head 变了但非祖先关系（rebase/squash/force-push）/ compare 失败降级 |
| `incremental` | 增量审查（仅改动文件的老 finding 被标记 resolved，新 finding + 未动文件老 finding 合并展示） | head 变了且是祖先关系（正常 push 新 commit） |
| `reuse` | 复用上次审查 | **不落库**。同 commit_sha 二次触发走 `_handle_reuse`，不新建 Review 行，只重发 note + commit status |

> `reuse` 模式不写入 DB — 这是设计决策（避免同 head 产生 N 份重复历史）。schema Literal 包含它只是为了展示层对齐。

---

## 5. Review.review_kind — 审查来源

`Review.review_kind` 列定义在 [models/review.py](../backend/models/review.py#L95-L99)，`server_default='mr'`。

| review_kind | 含义 | mr_iid | 落库？ |
|------------|------|--------|--------|
| `mr` | MR 事件触发（webhook） | 有值 | 是 |
| `commit` | Push Hook 逐 commit 审查 | `NULL` | **不落库** |

---

## 6. GitLab Commit Status — Pipeline 状态（独立于 Review.status）

GitLab 的 commit status（`set_commit_status`）是推送到 GitLab 的 pipeline 状态，与 `Review.status` 是**完全不同**的概念：

| GitLab state | 含义 | 何时推送 |
|-------------|------|----------|
| `success` | 审查通过（无 blocker） | `has_blocker=False` |
| `failed` | 审查有阻断级 finding | `has_blocker=True` |
| `pending` | — | **当前不推送**（GitLab API Literal 包含但代码未使用） |
| `running` | — | **当前不推送** |
| `canceled` | — | **当前不推送** |
| `skipped` | — | **当前不推送** |

推送逻辑统一为：`state = "failed" if has_blocker else "success"`

### 6.1 has_blocker 的计算

```python
has_blocker, blocker_count = compute_has_blocker(findings, block_policy)
```

- `findings`：本次审查的合并 finding 集合（new + carried_over）
- `block_policy`：按 target_branch 匹配的阻断策略（`match_block_policy`）
- 只有 `severity >= block_policy.block_severity` 的 finding 才算 blocker

---

## 7. 完整事件链路：MR open → close → reopen

以一次完整的 MR 生命周期为例，展示 Review、Finding、GitLab status 的联动：

```
1. MR open（commit_sha=head-a）
   ├─ Review: {status=done, lifecycle_event=NULL, has_blocker=True, finding_count=2, review_mode=full}
   ├─ Finding: #1 {status=open, severity=BLOCKER}  ← blocker
   ├─ Finding: #2 {status=open, severity=WARNING}
   └─ GitLab status: failed（has_blocker=True）

2. MR close
   ├─ Review (lifecycle): {status=done, lifecycle_event=mr_closed, has_blocker=False, finding_count=0}
   ├─ Finding #1: {status=mr_closed, resolved_in_review_id=lifecycle_review}
   ├─ Finding #2: {status=mr_closed, resolved_in_review_id=lifecycle_review}
   └─ GitLab status: 不推送（lifecycle 分支不调 set_commit_status）

3a. MR reopen（commit_sha=head-a，同 commit） ← 当前 Bug 场景
   ├─ Finding #1: {status=open}（reopen_mr_closed 翻回）
   ├─ Finding #2: {status=open}
   ├─ _plan_review → find_last_review_in_mr → 命中 lifecycle mr_closed review（Bug！）
   ├─ mode=reuse, parent=lifecycle_review
   ├─ _handle_reuse: has_blocker=False（lifecycle 恒为 False）, 0 findings
   └─ GitLab status: success ← ❌ 应为 failed

3b. MR reopen（commit_sha=head-b，新 commit） ← 正常场景
   ├─ Finding #1: {status=open}
   ├─ Finding #2: {status=open}
   ├─ _plan_review → find_last_review_in_mr → 命中 lifecycle review（同样有隐患）
   ├─ mode=incremental/full, parent=lifecycle_review
   ├─ engine 运行，产出新 findings
   ├─ 合并：new_findings + carried_over_untouched
   ├─ has_blocker = compute_has_blocker(combined_findings) ← 正确计算
   └─ GitLab status: failed（如果 combined 中有 BLOCKER）← ✓ 正确
```

### 7.1 Bug 根因

`find_last_review_in_mr`（[repositories/review.py#L89](../backend/repositories/review.py#L89)）查询只按 `status` 过滤，**不排除 `lifecycle_event IS NOT NULL` 的记账行**：

```python
conditions = [Review.project_id == project_id, Review.mr_iid == mr_iid]
if exclude_status:
    conditions.append(Review.status.notin_(exclude_status))
# ← 缺少: Review.lifecycle_event.is_(None)
```

lifecycle 记账行的 `status="done"` 不在 `exclude_status=("pending",)` 中，因此会被当作"上次审查"返回，导致：
- **同 commit reopen**：误走 reuse 分支，用 lifecycle 的 `has_blocker=False` 设 pipeline=success
- **新 commit reopen**：误用 lifecycle 作为增量 parent（base_sha 指向 lifecycle 的 commit_sha，虽然值碰巧相同，但语义错误）

### 7.2 已排除 lifecycle 的查询（正确做法参考）

`stats.py` 中所有 review 聚合查询都已正确排除 lifecycle 记账行：

```python
.where(Review.lifecycle_event.is_(None))  # ← stats 模块统一排除
```

---

## 8. 状态值速查表

### Review.status

| 值 | DB 中实际出现 | 写入者 |
|----|-------------|--------|
| `pending` | 理论上（model default），orchestrator 绕过 | model default |
| `running` | 不会出现 | — |
| `done` | 是 | orchestrator `_persist_review` + `_handle_lifecycle_event` |
| `engine_error` | 是 | orchestrator `_handle_engine_error` → `_persist_review` |
| `failed` | 不会出现 | — |

### Review.lifecycle_event

| 值 | 产生条件 |
|----|----------|
| `NULL` | 普通 MR 审查（open / update / reopen） |
| `mr_closed` | MR close webhook |
| `mr_merged` | MR merge webhook |

### Finding.status

| 值 | 产生条件 |
|----|----------|
| `open` | engine 新产出 / MR reopen 翻回 |
| `mr_closed` | MR close 事件 |
| `resolved` | MR merge / 增量审查改动文件老 finding 被替代 |

### CommitReviewResult.status（仅内存返回，不落库）

| 值 | 产生条件 |
|----|----------|
| `done` | commit/push 审查正常完成 |
| `engine_error` | commit/push 审查引擎异常 |
| `skipped_merge_commit` | merge commit（>1 parent）跳过 |
| `skipped_root_commit` | 根 commit（无 parent）跳过 |
| `skipped_disabled` | 全局/项目级 commit_review 开关关闭 |
| `skipped_no_changes` | push 审查 compare 失败，无 diff 可审 |
