"""Review planning: decide full / incremental / reuse mode for an MR event.

reuse 模式的执行（``_handle_reuse``）已迁往 :mod:`lifecycle`（PR3）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import SQLAlchemyError

from integrations.gitlab.client import GitLabClient, GitLabClientError
from repositories.project import ProjectRepository
from repositories.review import ReviewRepository
from services.review_orchestration.events import GitLabMergeRequestEvent, GitLabPushEvent
from services.review_orchestration.results import _ReviewPlan

if TYPE_CHECKING:
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)


async def _plan_review(
    event: GitLabMergeRequestEvent,
    *,
    session_factory: SessionFactory | None,
    gitlab_client: GitLabClient,
) -> _ReviewPlan:
    """按 (project, mr_iid) 决定这次评审模式。

    决策路径：
      - session_factory 未接入 / Project 未注册 → full，无 parent，保留旧 MVP 行为。
      - 同 MR 无上一次 review → full。
      - 同 MR 上一次 review 的 head == 本次 head → reuse。
      - head 变了：调 GitLab compare（prev_head..new_head）拿"祖先关系 +
        改动文件集合"：
        - commits 非空 且能拿到 changed_files → incremental，base=上次 head，
          parent=上次 review.id，plan.changed_files=改动文件 new_path 集合。
        - 不是祖先关系（rebase/squash/force-push）/ compare 失败 → full 降级，
          parent 仍串起来，reason=history_rewritten 或 compare_failed。

    任何 DB 异常都吞成 full 降级，绝不能阻断主流程。
    """

    default_full = _ReviewPlan(
        mode="full",
        base_sha=event.target_commit_sha,
        parent_review_id=None,
        reason="first_review_or_no_db",
    )
    if session_factory is None:
        return default_full
    try:
        async with session_factory() as session:
            project_repo = ProjectRepository(session)
            project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
            if project is None:
                return default_full
            review_repo = ReviewRepository(session)
            # 排除 pending：未完成的评审不适合当增量起点。
            last = await review_repo.find_last_review_in_mr(
                project.id,
                str(event.mr_iid),
                exclude_status=("pending",),
            )
    except SQLAlchemyError:
        logger.exception(
            "_plan_review DB lookup failed",
            extra={"gitlab_project_id": event.project_id, "mr_iid": event.mr_iid},
        )
        return default_full

    if last is None:
        return default_full

    if last.commit_sha == event.source_commit_sha:
        return _ReviewPlan(
            mode="reuse",
            base_sha=last.base_sha or event.target_commit_sha,
            parent_review_id=last.id,
            reason="same_head_ci_retry",
        )

    # head 变了 → 用 GitLab compare 一次拿"祖先关系 + 改动文件集合"。
    is_ancestor, changed_files = await _fetch_ancestor_and_changed_files(
        gitlab_client=gitlab_client,
        project_id=event.project_id,
        older_sha=last.commit_sha,
        newer_sha=event.source_commit_sha,
    )
    if is_ancestor and changed_files is not None:
        return _ReviewPlan(
            mode="incremental",
            base_sha=last.commit_sha,
            parent_review_id=last.id,
            reason="head_advanced",
            changed_files=changed_files,
        )
    return _ReviewPlan(
        mode="full",
        base_sha=event.target_commit_sha,
        parent_review_id=last.id,
        reason="history_rewritten" if not is_ancestor else "compare_missing_files",
    )


async def _fetch_ancestor_and_changed_files(
    *,
    gitlab_client: GitLabClient,
    project_id: int,
    older_sha: str,
    newer_sha: str,
) -> tuple[bool, frozenset[str] | None]:
    """一次 GitLab compare 调用，同时判祖先关系并抽出改动文件 new_path 集合。

    走 ``/repository/compare?from=older&to=newer&straight=true``：

    - ``commits`` 数组非空 + 无 ``error`` → older 是 newer 的祖先。
    - ``diffs`` 数组：从中收集 ``new_path``（deleted_file 则收集 old_path），
      得到本次 push 涉及的文件集合。

    异常 / 权限 / 404 一律返回 ``(False, None)``，让上层保守降级到 full。
    祖先关系为 True 但 diffs 拿不到（异常 payload / 空数组）时返回
    ``(True, None)``，同样降级到 full —— 增量语义依赖"知道改了哪些文件"，
    拿不到就不做半吊子的事。
    """

    try:
        payload = await gitlab_client.compare_refs(
            project_id=project_id,
            from_sha=older_sha,
            to_sha=newer_sha,
        )
    except GitLabClientError:
        logger.warning(
            "compare_refs failed; conservatively falling back to full review",
            extra={
                "gitlab_project_id": project_id,
                "from_sha": older_sha,
                "to_sha": newer_sha,
            },
        )
        return False, None
    except Exception:
        logger.exception(
            "compare_refs raised unexpectedly; falling back to full review",
            extra={
                "gitlab_project_id": project_id,
                "from_sha": older_sha,
                "to_sha": newer_sha,
            },
        )
        return False, None
    if payload.get("error"):
        return False, None
    commits = payload.get("commits")
    if not isinstance(commits, list) or len(commits) == 0:
        return False, None
    raw_diffs = payload.get("diffs")
    if not isinstance(raw_diffs, list):
        return True, None
    changed: set[str] = set()
    for item in raw_diffs:
        if not isinstance(item, dict):
            continue
        new_path = str(item.get("new_path") or "").strip()
        old_path = str(item.get("old_path") or "").strip()
        if item.get("deleted_file"):
            if old_path:
                changed.add(old_path)
            continue
        if new_path:
            changed.add(new_path)
        elif old_path:
            # 极端保险：new_path 缺失但 old_path 有 —— 也算改过。
            changed.add(old_path)
    if not changed:
        # 有 commits 但拿不到文件（罕见）→ 保守降级 full。
        return True, None
    return True, frozenset(changed)


async def _fetch_changes_for_plan(
    event: GitLabMergeRequestEvent,
    plan: _ReviewPlan,
    *,
    gitlab_client: GitLabClient,
) -> dict[str, Any]:
    """按 plan.mode 取本次要送引擎的 GitLab changes payload。

    - full / reuse：直接走 MR changes 端点，语义等价旧路径。
    - incremental：仍走 MR changes 拿 base..head 完整 changes，再按
      ``plan.changed_files`` 过滤 changes 数组 —— 保证送给 LLM 的是
      "本次 push 改动的文件、但 diff 是完整 base..head 全量"。

    过滤后 changes 变空时保留其它字段（如 diff_refs）不动，让下游 pipeline
    继续走完（findings 为空，note 会写"无可审内容"）。
    """

    raw = await gitlab_client.get_merge_request_changes(
        project_id=event.project_id,
        mr_iid=event.mr_iid,
    )
    if plan.mode != "incremental" or plan.changed_files is None:
        return raw
    changed_files = plan.changed_files
    raw_changes = raw.get("changes")
    if not isinstance(raw_changes, list):
        return raw
    filtered: list[Any] = []
    for item in raw_changes:
        if not isinstance(item, dict):
            continue
        new_path = str(item.get("new_path") or "")
        old_path = str(item.get("old_path") or "")
        # 匹配 new_path 优先（新增/修改文件），deleted_file 匹配 old_path。
        if new_path and new_path in changed_files:
            filtered.append(item)
        elif old_path and old_path in changed_files:
            filtered.append(item)
    return {**raw, "changes": filtered}


async def _fetch_push_changes(
    event: GitLabPushEvent,
    *,
    gitlab_client: GitLabClient,
) -> dict[str, Any] | None:
    """取一次 push 的合并变更（``{"changes": [...]}`` 形态，供 hunk 构建复用）。

    - 新建分支（``before_sha`` 为 40 个 0）：降级用 head commit 的 diff；
    - 常规 push：``compare_refs(before, after)`` 一次拉取；``diffs`` 数组的
      元素结构与 MR changes 的 ``changes`` 一致（old_path/new_path/diff/
      new_file/deleted_file），交给 :meth:`_build_diff_hunks` 复用。
    - compare 返回 ``error`` / 结构异常 / 任何 API 异常：记 warning 并返回
      ``None``，上层按 ``skipped_no_changes`` 处理（不误报审查失败）。

    Returns:
        ``{"changes": [...]}``；获取失败时 ``None``。
    """

    if event.before_sha == "0" * 40:
        # 新建分支：before 不存在，无法 compare，用 head commit 的 diff。
        try:
            diffs = await gitlab_client.get_commit_diff(
                project_id=event.project_id,
                sha=event.after_sha,
            )
        except Exception:
            logger.exception(
                "push review failed to fetch head commit diff; skipping push",
                extra={
                    "gitlab_project_id": event.project_id,
                    "branch": event.branch,
                    "after_sha": event.after_sha,
                },
            )
            return None
        return {"changes": diffs}

    try:
        payload = await gitlab_client.compare_refs(
            project_id=event.project_id,
            from_sha=event.before_sha,
            to_sha=event.after_sha,
        )
    except Exception:
        logger.exception(
            "push review compare failed; skipping push",
            extra={
                "gitlab_project_id": event.project_id,
                "branch": event.branch,
                "before_sha": event.before_sha,
                "after_sha": event.after_sha,
            },
        )
        return None
    if payload.get("error"):
        logger.warning(
            "push review compare returned error; skipping push",
            extra={
                "gitlab_project_id": event.project_id,
                "branch": event.branch,
                "before_sha": event.before_sha,
                "after_sha": event.after_sha,
                "error": str(payload.get("error")),
            },
        )
        return None
    raw_diffs = payload.get("diffs")
    if not isinstance(raw_diffs, list):
        return None
    return {"changes": [item for item in raw_diffs if isinstance(item, dict)]}
