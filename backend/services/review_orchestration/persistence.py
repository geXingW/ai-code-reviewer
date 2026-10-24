"""Review persistence: 落库 ``reviews`` / ``review_findings`` 与增量 finding 合并。

两条审查链路（MR / push+commit）的落库语义完全一致：

* **best-effort** —— ``session_factory`` 为 None（MVP 兼容路径）直接跳过；
  Project 未在管理后台注册则记 warning 跳过；事务失败 rollback + warning，
  不影响 GitLab 反馈与 API 响应。
* **一次事务** —— 行构造 / flush / mark_resolved / commit 全部下沉到
  :meth:`ReviewRepository.create_review_with_findings`。

差异只有字段映射（``mr_iid`` / ``review_kind`` / ``base_sha``），因此公共流程
收敛在 :func:`_write_review`，两个公开函数只负责构造 ``ReviewRow``。

历史：MR 生命周期动作（close / merge / reopen）在 :mod:`lifecycle`。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from engines import Finding
from models.finding import Finding as FindingRow
from models.review import Review as ReviewRow
from repositories.project import ProjectRepository
from repositories.review import FindingRepository, ReviewRepository
from services.review_orchestration.diff_utils import _finding_row_to_engine
from services.review_orchestration.events import (
    GitLabMergeRequestEvent,
    GitLabPushEvent,
    _CommitLikeEvent,
)
from services.review_orchestration.results import _MergeResult, _ReviewPlan

if TYPE_CHECKING:
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)


def _align_comment_ids(
    comment_ids: Sequence[str | None] | None,
    expected: int,
    *,
    message: str,
    extra: dict[str, object],
) -> list[str | None]:
    """把行级评论 id 对齐到 ``findings`` 长度。

    长度不一致属于上游 bug（理论不会发生），此时**丢弃全部 id** 而不是让
    ``zip`` 静默截断——错配的 discussion id 会在误报闭环里 resolve 掉别人的评论。
    """

    if comment_ids is None:
        return [None] * expected
    aligned = list(comment_ids)
    if len(aligned) != expected:
        logger.warning(message, extra={**extra, "expected": expected, "got": len(aligned)})
        return [None] * expected
    return aligned


async def _write_review(
    *,
    gitlab_project_id: int,
    review_row: ReviewRow,
    findings: Sequence[Finding],
    comment_ids: Sequence[str | None] | None,
    stale_finding_ids: Sequence[UUID],
    session_factory: SessionFactory | None,
    skip_log_message: str,
    failure_log_message: str,
    failure_extra: dict[str, object],
) -> None:
    """把一条 review 及其 findings 写入数据库（best-effort，异常不外抛）。"""

    if session_factory is None:
        return
    ids = _align_comment_ids(
        comment_ids,
        len(findings),
        message="comment ids length mismatch; discarding ids to avoid misalignment",
        extra={"review_id": str(review_row.id)},
    )
    try:
        async with session_factory() as session:
            project = await ProjectRepository(session).get_by_gitlab_project_id(
                str(gitlab_project_id)
            )
            if project is None:
                logger.warning(
                    skip_log_message,
                    extra={
                        "gitlab_project_id": gitlab_project_id,
                        "review_id": str(review_row.id),
                    },
                )
                return
            review_row.project_id = project.id
            await ReviewRepository(session).create_review_with_findings(
                review=review_row,
                findings=list(zip(findings, ids, strict=True)),
                stale_finding_ids=list(stale_finding_ids),
                resolved_in_review_id=review_row.id,
            )
    except SQLAlchemyError:
        logger.exception(failure_log_message, extra=failure_extra)


async def _persist_review(
    *,
    event: GitLabMergeRequestEvent,
    review_id: UUID,
    findings: Sequence[Finding],
    has_blocker: bool,
    status_value: str,
    duration_ms: int,
    engine_used: str,
    plan: _ReviewPlan,
    merge: _MergeResult | None,
    combined_finding_count: int,
    discussion_ids: Sequence[str | None] | None,
    session_factory: SessionFactory | None,
) -> None:
    """Best-effort 落库 MR 审查：写入 ``reviews`` + ``review_findings`` 两张表。

    - ``session_factory`` 为 None：跳过（MVP 兼容路径）。
    - Project 不存在（GitLab 项目未在管理后台注册）：跳过并记 warning。
    - 事务失败：rollback + 记 warning，不影响 GitLab 反馈与 API 响应。

    增量语义（feat/rescan-changed-files）：

      - ``findings`` = 本轮 engine 输出。改动文件全量重审，全部当作新增
        行入库，``first_seen_review_id=review_id``。
      - ``merge.stale_findings_to_resolve`` 通过 ``mark_resolved`` 批量
        UPDATE 老 finding 的 status='resolved' + resolved_in_review_id。
      - ``discussion_ids`` 与 ``findings`` 同序，命中的写回
        ``gitlab_discussion_id``，供后续改动重审时定向 resolve。
      - ``review.finding_count`` 使用 ``combined_finding_count`` —— 与
        GitLab note / commit status 描述保持一致（合并后的总数）。
    """

    # merge 为 None（engine_error）时 findings 就是"新 finding"的全部（一般是空）。
    stale_rows: Sequence[FindingRow] = (
        merge.stale_findings_to_resolve if merge is not None else ()
    )
    review_row = ReviewRow(
        id=review_id,
        mr_iid=str(event.mr_iid),
        source_branch=event.source_branch,
        target_branch=event.target_branch,
        commit_sha=event.source_commit_sha,
        status=status_value,
        engine_used=engine_used,
        has_blocker=has_blocker,
        finding_count=combined_finding_count,
        duration_ms=duration_ms,
        base_sha=plan.base_sha,
        parent_review_id=plan.parent_review_id,
        review_mode=plan.mode,
    )
    await _write_review(
        gitlab_project_id=event.project_id,
        review_row=review_row,
        findings=findings,
        comment_ids=discussion_ids,
        stale_finding_ids=[row.id for row in stale_rows],
        session_factory=session_factory,
        skip_log_message="skip review persistence: project not registered",
        failure_log_message="failed to persist review",
        failure_extra={
            "gitlab_project_id": event.project_id,
            "review_id": str(review_id),
            "mr_iid": event.mr_iid,
        },
    )


async def _persist_commit_style_review(
    *,
    event: _CommitLikeEvent,
    review_id: UUID,
    findings: Sequence[Finding],
    has_blocker: bool,
    status_value: str,
    duration_ms: int,
    engine_used: str,
    base_sha: str | None,
    comment_ids: Sequence[str | None] | None,
    session_factory: SessionFactory | None,
) -> None:
    """Best-effort 落库：push / commit 审查写 ``reviews`` + ``review_findings``。

    与 MR 链路（:func:`_persist_review`）共享 :func:`_write_review`，差异在
    字段映射：

    - ``mr_iid`` 落 ``None``（commit 级审查没有 MR 概念，schema 已允许）；
    - ``review_kind`` 落事件链路的来源标记（``'commit'`` / ``'push'``）；
    - ``policy_applied`` 落 ``None``：commit 链路的 policy_applied 是展示
      字符串（如 ``"master -> BLOCKER"``），而列存的是策略主键 UUID，
      语义不匹配，不强行写入；
    - ``base_sha`` 落本次 diff 起点（commit 链路为第一个 parent，push 链路
      为 before SHA）；
    - ``comment_ids`` 是 GitLab commit 评论 id 列表（与 findings 同序），
      写入 ``gitlab_discussion_id`` 列复用现有语义（行级评论的 GitLab 定位
      标识），供误报闭环 resolve 时使用。
    """

    review_kind = _commit_style_review_kind(event)
    review_row = ReviewRow(
        id=review_id,
        mr_iid=None,
        source_branch=event.branch,
        target_branch=event.branch,
        commit_sha=event.commit_sha,
        status=status_value,
        engine_used=engine_used,
        has_blocker=has_blocker,
        finding_count=len(findings),
        duration_ms=duration_ms,
        base_sha=base_sha,
        review_mode="full",
        review_kind=review_kind,
    )
    await _write_review(
        gitlab_project_id=event.project_id,
        review_row=review_row,
        findings=findings,
        comment_ids=comment_ids,
        stale_finding_ids=(),
        session_factory=session_factory,
        skip_log_message="skip commit-style review persistence: project not registered",
        failure_log_message="failed to persist commit-style review",
        failure_extra={
            "gitlab_project_id": event.project_id,
            "review_id": str(review_id),
            "review_kind": review_kind,
        },
    )


def _commit_style_review_kind(event: _CommitLikeEvent) -> str:
    """从事件类型推断 review_kind（commit / push 链路的落库来源标记）。"""

    if isinstance(event, GitLabPushEvent):
        return "push"
    return "commit"


async def _merge_findings_for_plan(
    event: GitLabMergeRequestEvent,
    plan: _ReviewPlan,
    new_findings: Sequence[Finding],
    *,
    session_factory: SessionFactory | None,
) -> _MergeResult:
    """按 plan.mode 决定要不要把历史 open findings 与本次 engine 输出合并。

    **新语义（feat/rescan-changed-files）：改动文件级换代**。

    - ``full`` / 无 session_factory：无历史概念，findings 全部当新增。
    - ``incremental``：
        1. 拉本 MR 所有 ``status='open'`` 的历史 findings；
        2. 按 ``plan.changed_files`` 分两组：
           - **属于改动文件**（含 old_path 命中，覆盖 renamed / deleted 时的
             老 finding）→ ``stale_findings_to_resolve``：本函数不改 DB，
             交给 :meth:`_resolve_stale_discussions_for_files` 关 GitLab
             discussion + :func:`_persist_review` 事务里 UPDATE status；
           - **不属于改动文件** → ``carried_over_untouched``：保持 open，note
             里作为"历史遗留"展示。
        3. 新 findings 全部当作本轮新增（``new_findings``）。
        4. combined 顺序：新增在前 + 未动文件历史在后。

    session_factory 缺失 / DB 异常 / project 未注册 → 空历史，等价 full 行为。
    """

    empty = _MergeResult(
        combined_findings=list(new_findings),
        new_findings=list(new_findings),
        carried_over_untouched=[],
        stale_findings_to_resolve=[],
    )
    if plan.mode != "incremental" or plan.changed_files is None:
        return empty
    if session_factory is None:
        return empty
    try:
        async with session_factory() as session:
            project_repo = ProjectRepository(session)
            project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
            if project is None:
                return empty
            finding_repo = FindingRepository(session)
            old_open = await finding_repo.list_open_by_mr(project.id, str(event.mr_iid))
    except SQLAlchemyError:
        logger.exception(
            "merge findings lookup failed; behaving as if history is empty",
            extra={"gitlab_project_id": event.project_id, "mr_iid": event.mr_iid},
        )
        return empty

    if not old_open:
        return empty

    changed = plan.changed_files
    stale_rows: list[FindingRow] = []
    carried_untouched: list[Finding] = []
    for row in old_open:
        if row.file_path in changed:
            stale_rows.append(row)
        else:
            carried_untouched.append(_finding_row_to_engine(row))

    new_list = list(new_findings)
    combined = new_list + carried_untouched
    return _MergeResult(
        combined_findings=combined,
        new_findings=new_list,
        carried_over_untouched=carried_untouched,
        stale_findings_to_resolve=stale_rows,
    )
