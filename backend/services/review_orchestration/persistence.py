"""Review persistence: 落库 reviews / review_findings 与增量 finding 合并。

MR 生命周期动作（close / merge / reopen）已迁往 :mod:`lifecycle`（PR3）。
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
from repositories.review import FindingRepository
from services.review_orchestration.diff_utils import _finding_row_to_engine
from services.review_orchestration.events import GitLabMergeRequestEvent
from services.review_orchestration.results import _MergeResult, _ReviewPlan

if TYPE_CHECKING:
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)


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
    """Best-effort 落库：写入 ``reviews`` + ``review_findings`` 两张表。

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

    if session_factory is None:
        return
    # merge 为 None（engine_error）时 findings 就是"新 finding"的全部（一般是空）。
    new_findings_to_persist: Sequence[Finding] = findings
    stale_rows: Sequence[FindingRow] = (
        merge.stale_findings_to_resolve if merge is not None else ()
    )
    # discussion_ids 与 findings 同序对齐；若上游未产出（engine_error），全部
    # 视为 None，保证 zip 长度对齐。
    ids_seq: Sequence[str | None] = (
        list(discussion_ids)
        if discussion_ids is not None
        else [None] * len(new_findings_to_persist)
    )
    if len(ids_seq) != len(new_findings_to_persist):
        # 理论不会发生；发生就丢弃 discussion_ids 而不是让 zip 静默截断。
        logger.warning(
            "discussion_ids length mismatch; discarding ids to avoid misalignment",
            extra={
                "expected": len(new_findings_to_persist),
                "got": len(ids_seq),
            },
        )
        ids_seq = [None] * len(new_findings_to_persist)
    try:
        async with session_factory() as session:
            project_repo = ProjectRepository(session)
            project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
            if project is None:
                logger.warning(
                    "skip review persistence: project not registered",
                    extra={
                        "gitlab_project_id": event.project_id,
                        "review_id": str(review_id),
                    },
                )
                return
            review_row = ReviewRow(
                id=review_id,
                project_id=project.id,
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
            session.add(review_row)
            # flush 一下让 review 主键先落，随后 update / insert 老 finding 才有 FK 目标。
            await session.flush()
            for finding, discussion_id in zip(
                new_findings_to_persist, ids_seq, strict=True,
            ):
                session.add(
                    FindingRow(
                        review_id=review_id,
                        file_path=finding.file_path,
                        line_number=finding.line_number,
                        rule_id=finding.rule_id or "unknown",
                        severity=finding.severity,
                        title=finding.title,
                        description=finding.description,
                        suggestion=finding.suggestion,
                        existing_code=finding.existing_code,
                        # LLM 输出的分类原样落库；缺失/无效不做兜底——渲染层
                        # 会 fallback 到 rule_id 推断，避免在这里错误锁死。
                        category=finding.category,
                        confidence=float(finding.confidence or 0.0),
                        # 本次新出现的 finding：first_seen 指向自己。
                        first_seen_review_id=review_id,
                        status="open",
                        gitlab_discussion_id=discussion_id,
                    )
                )
            if stale_rows:
                finding_repo = FindingRepository(session)
                await finding_repo.mark_resolved(
                    [row.id for row in stale_rows],
                    review_id,
                )
            await session.commit()
    except SQLAlchemyError:
        logger.exception(
            "failed to persist review",
            extra={
                "gitlab_project_id": event.project_id,
                "review_id": str(review_id),
                "mr_iid": event.mr_iid,
            },
        )


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
             discussion + :meth:`_persist_review` 事务里 UPDATE status；
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

