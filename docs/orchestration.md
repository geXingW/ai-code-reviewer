# 审查编排（Orchestration）

> 本文描述**一次审查从触发到落库**的完整链路：阶段顺序、数据结构、会话边界、错误路径。
> 代码位置：`backend/services/review_orchestration/`。架构全景见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 1. 三个入口，一条主干

| 入口 | 触发方 | 入口函数 | 事件类型 |
|---|---|---|---|
| GitLab MR Webhook | GitLab | `api/gitlab_webhook.py` → `ReviewOrchestrator.review_merge_request` | `GitLabMergeRequestEvent` |
| GitLab Push Hook | GitLab | `api/gitlab_webhook.py` → `review_push` / `review_commit` | `GitLabPushEvent` / `GitLabCommitEvent` |
| Jenkins 同步调用 | `POST /api/reviews` | `api/reviews.py` → 复用 webhook 的事件构造 | `GitLabMergeRequestEvent` |

三条入口共同点：**先把 payload 归一化成 frozen dataclass 事件**（`events.py`），再进入编排层。
归一化之后，编排层不再关心 HTTP / payload 细节。

`events.py` 里的字段分两类：

- **事实字段**：`project_id` / `mr_iid` / `*_sha` / 分支 / 标题 / 作者 / 时间。
- **派生属性**：`project_uuid`（由 GitLab 数字 ID 派生的稳定 UUID）、`commit_sha`
  （push 事件取 `after_sha`）、`title` / `web_url`（push 事件取最后一个 commit）。
  后三个是为了让 commit / push / MR 三类事件共享同一批写回函数（duck typing）。

## 2. MR 链路阶段

| # | 阶段 | 实现 | 跳过/短路条件 |
|---|---|---|---|
| 1 | 生命周期事件 | `lifecycle/event.py` → `actions.py` | close/merge 直接返回；reopen 落库后继续 |
| 2 | 阻断策略匹配 | `policy.py::resolve_policy_or_skip` | 分支未命中任何策略 → `skipped_no_policy` |
| 3 | 评审模式决策 | `planning.py::_plan_review` | 产出 `full` / `incremental` / `reuse` |
| 4 | reuse 分支 | `lifecycle/reuse.py::_handle_reuse` | 上次结果可复用则不跑 engine；失效则降级 full |
| 5 | 拉 diff | `planning.py::_fetch_changes_for_plan` | 空 diff 仍继续（产出 0 findings） |
| 6 | 规则 / 负样本 / Provider / 引擎解析 | `resolution/*` | 全部 fail-open：查不到就退回默认 |
| 7 | 构建上下文 | `engines.types.ReviewContext` | `repo_reader` 只在 agent 引擎使用 |
| 8 | 引擎调用 | `EngineRegistry.get(name).review(ctx)` | 抛异常 → `engine_error.py` 分支 |
| 9 | 增量合并 | `persistence.py::_merge_findings_for_plan` | 仅 `incremental` 模式 |
| 10 | 阻断判定 | `core/block_policy.py::compute_has_blocker` | — |
| 11 | GitLab 写回 | `gitlab_feedback.py` | 先关旧 discussion，再发新行级评论 |
| 12 | 汇总评论 + commit status | `core/summary_builder.py` + `GitLabClient` | — |
| 13 | 落库 | `persistence.py::_persist_review` | best-effort，失败只记日志 |
| 14 | 通知 | `notification.py` → `services/notification_service.py` | best-effort |

commit / push 链路走 `handlers/base.py` 的模板方法，阶段 1–14 中除 3/4/9（无增量概念）
与 5（diff 语义不同）外完全同构。

## 3. 关键数据结构

```python
# results.py —— 全部为 frozen dataclass，跨模块传递时不可变
_ReviewPlan(mode, base_sha, parent_review_id, reason, changed_files)
_MergeResult(combined_findings, new_findings, carried_over_untouched, stale_findings_to_resolve)
OrchestratorResult(review_id, status, finding_count, has_blocker, blocker_count, policy_applied, note_id)
CommitReviewResult(review_id, project_uuid, status, finding_count, has_blocker, note_id)
```

`_ReviewPlan.mode` 的语义（易混淆，重点）：

| mode | diff 范围 | engine 跑不跑 | 历史 finding |
|---|---|---|---|
| `full` | `base..head` 全量 | 跑 | 不动 |
| `incremental` | `base..head` 全量，但**只审本次 push 改动的文件** | 跑 | 改动文件的历史 finding 整批 resolve（"整体换代"） |
| `reuse` | 不拉 diff | 不跑 | 沿用父 review 结果重发反馈 |

## 4. 会话边界与失败语义

- 每次审查会打开 **多次** 独立会话（策略解析、计划、规则、Provider、历史、落库、通知各一次），
  属于"读多写少、以只读配置为主"的务实取舍；写操作只在落库阶段发生，且是单个事务。
  已知代价：审查期间若管理员改动规则/策略，前半段与后半段可能读到不同快照。
- 所有 **best-effort** 动作（落库、通知、关闭历史 discussion）失败都不改变返回值，
  只有 engine 抛异常会改变审查状态。
- 引擎失败时的确定性反馈：
  - MR：按 `block_on_engine_error` 决定 commit status（`failed` / `success`），汇总评论置顶
    `⚠️ AI Review FAILED` 横幅，并落一条 `status='engine_error'` 的 review 行。
  - commit / push：commit status 恒为 `failed`（失败不能装成功），同样落库 + 通知。

## 5. 演进方向（未实施）

1. **收敛双管线**：把 MR 也接入 `ReviewCommitStyleHandler` 的模板方法骨架，
   三个 handler 只保留 `fetch_changes` / `build_context` / `build_note` / `write_back` 差异。
   阻塞点：MR 独有的增量合并与 reuse 需要在骨架里开两个可选钩子。
2. **参数对象**：`_persist_review`（12 参）、`build_review_summary_note`（18 参）等改用
   `ReviewOutcome` / `FeedbackTarget`，消除"同一组事实手搬 10+ 次"。
3. **Unit of Work**：一次审查共享一个会话/事务，同时消除重复的 project 查询
   （当前同一次 MR 审查会查 4–5 次 `projects`）。

详细问题清单与优先级见 [code-quality-review-2026-10-24.md](code-quality-review-2026-10-24.md)。
