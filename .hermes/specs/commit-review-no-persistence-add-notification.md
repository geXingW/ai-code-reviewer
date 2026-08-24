# Commit Review 改造 Spec

## 背景

`review_commit` 方法（`backend/services/review_orchestrator.py` 472-700 行）实现了 Push Hook 逐 commit 审查，但存在两个问题：

1. **commit 审查结果被记录到系统**：`_persist_commit_review` 会把 review + findings 写入 `reviews` 和 `review_findings` 表，但用户要求只记录 MR 审查结果，不记录 commit 审查结果。
2. **commit 审查完成后没有推送钉钉通知**：MR 审查有 `_push_review_notification`（451 行/1118 行），但 commit 审查全程没有调用。

## 改动范围

### 文件：`backend/services/review_orchestrator.py`

#### 改动 1：移除 commit 审查的持久化记录

**位置**：`review_commit` 方法中三处 `_persist_commit_review` 调用：

- **空 diff 分支**（第 585-595 行）：删除 `await self._persist_commit_review(...)` 整个调用块。
- **正常审查完成分支**（第 682-692 行）：删除 `await self._persist_commit_review(...)` 整个调用块。
- **engine_error 分支**（第 938-948 行）：删除 `await self._persist_commit_review(...)` 整个调用块。

注意：幂等检查 `_find_completed_commit_review`（第 507-522 行）也需要一起删除，因为没了持久化就不存在"已审查过"的记录，幂等检查没有意义。删除后该段改为直接跳过幂等检查，每次 push 都重新审查 commit。

#### 改动 2：为 commit 审查添加钉钉通知推送

**位置**：`review_commit` 方法中三处需要推送通知的地方：

1. **空 diff 分支**（`await self._gitlab_client.set_commit_status(...)` 之后，`return CommitReviewResult(...)` 之前）：
   ```python
   await self._push_commit_review_notification(
       event=event,
       review_id=review_id,
       finding_count=0,
       has_blocker=False,
       blocker_count=0,
       status_value="done",
       findings=[],
   )
   ```

2. **正常审查完成分支**（`await self._gitlab_client.set_commit_status(...)` 之后，`return CommitReviewResult(...)` 之前）：
   ```python
   await self._push_commit_review_notification(
       event=event,
       review_id=review_id,
       finding_count=len(findings),
       has_blocker=has_blocker,
       blocker_count=blocker_count,
       status_value="done",
       findings=findings,
   )
   ```

3. **engine_error 分支**（`await self._gitlab_client.set_commit_status(...)` 之后，`return CommitReviewResult(...)` 之前）：
   ```python
   await self._push_commit_review_notification(
       event=event,
       review_id=review_id,
       finding_count=0,
       has_blocker=has_blocker,
       blocker_count=blocker_count,
       status_value="engine_error",
       findings=[],
   )
   ```

#### 改动 3：新增 `_push_commit_review_notification` 方法

在 `_push_review_notification` 方法附近（约 2208 行之后）添加，参照 `_push_review_notification` 实现，但 `review_data` 字段名用 commit 语义：

```python
async def _push_commit_review_notification(
    self,
    *,
    event: GitLabCommitEvent,
    review_id: UUID,
    finding_count: int,
    has_blocker: bool,
    blocker_count: int,
    status_value: str,
    findings: Sequence[Finding] | None = None,
) -> None:
    """推送 commit 审查完成通知（best-effort，失败不影响主流程）。

    参照 _push_review_notification，但 commit 审查没有 MR 上下文，
    mr_iid / mr_title 等字段用 commit 信息替代。
    """
    if self._notification_service is None:
        return
    try:
        await self._notification_service.send_review_completed(
            gitlab_project_id=event.project_id,
            review_data={
                "review_id": str(review_id),
                "mr_iid": event.commit_sha[:8],  # commit 短 SHA 作为标识
                "mr_title": event.title,          # commit message 首行
                "finding_count": finding_count,
                "has_blocker": has_blocker,
                "blocker_count": blocker_count,
                "detail_url": self._build_review_detail_url(review_id),
                "status": status_value,
                "mr_author_username": event.author_username,
                "mr_author_name": event.author_name,
                "mr_web_url": None,  # commit 没有 MR 链接
                "findings_summary": _build_findings_summary(findings or []),
                "mr_created_at": "",  # commit 没有创建时间
                "changed_files_count": 0,
            },
        )
    except Exception as exc:
        logger.warning("Failed to send commit review notification", exc_info=exc)
```

同时需要修改 `_build_review_message`（`notification_service.py`）使其能处理 commit 场景（`mr_web_url` 为 None、`mr_created_at` 为空时不崩溃），不过当前代码已经做了 None 检查，`_build_mr_section` 空字段会自动跳过，应该不需要额外改动。但需要确认 `mr_iid` 传入字符串不会导致 int() 转换异常。

检查 `_build_review_message` 240 行：`mr_iid = review_data.get("mr_iid")`，后续 `mr_label = f"!{mr_iid}"` 只是字符串拼接，不会做 int 转换，所以传短 SHA 字符串没问题。

#### 改动 4：删除 `_persist_commit_review` 方法

由于不再需要持久化 commit 审查，`_persist_commit_review` 方法（958-1050 行附近）也应删除，避免死代码。

#### 改动 5：删除 `_find_completed_commit_review` 方法

同上，不再需要幂等查询，删除该方法（801-831 行）。

### 改动 6：清理 `_post_commit_finding_comments` 方法

该方法（833-894 行）仍然需要保留，因为 commit 审查的 finding 行级评论仍然需要贴到 GitLab commit 上。这个不受影响。

### 改动 7：删除 `CommitReviewResult` 中的 `review_id` 字段或改为 Optional

既然不再持久化，`review_id` 仍然在 `uuid4()` 生成，但不再落库。可以保留 `review_id` 字段用于日志追踪和通知，只需确认 `CommitReviewResult` 的调用方不依赖 `review_id` 去查 DB。

---

## 验证要点

1. Push Hook 触发后，commit 审查仍然正常执行（跑 engine、贴 GitLab 评论、设置 commit status）
2. `reviews` 表不再新增 `review_kind='commit'` 的记录
3. `review_findings` 表不再新增 commit 审查的 finding
4. commit 审查完成后，钉钉收到通知（如果项目配置了钉钉渠道）
5. MR 审查功能不受影响（持久化 + 通知 都正常）
6. 测试 `backend/tests/test_commit_review.py` 需要同步更新