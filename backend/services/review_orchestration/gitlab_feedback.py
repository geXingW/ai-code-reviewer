"""GitLab write-back helpers: discussions, comments, stale resolution, detail URLs."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from core.summary_builder import build_finding_discussion_body
from engines import Finding
from integrations.gitlab.client import GitLabClient
from models.finding import Finding as FindingRow
from services.review_orchestration.diff_utils import (
    _extract_diff_refs,
    _is_line_number_valid_for_current_diff,
    _resolve_finding_paths,
)
from services.review_orchestration.events import GitLabMergeRequestEvent, _CommitLikeEvent

logger = logging.getLogger(__name__)


def _build_review_detail_url(
    *,
    review_detail_base_url: str | None,
    review_id: UUID,
) -> str | None:
    """Build an optional browser URL for the persisted review detail page."""

    if review_detail_base_url is None:
        return None
    return f"{review_detail_base_url}/reviews/{review_id}"


async def _post_finding_discussions(
    event: GitLabMergeRequestEvent,
    changes_payload: dict[str, Any],
    findings: Sequence[Finding],
    *,
    gitlab_client: GitLabClient,
) -> list[str | None]:
    """Post line-level GitLab discussions for findings with a valid location.

    Discussion creation is best-effort: a single stale line location should not
    prevent the summary note or commit status from being written back.

    Returns a list aligned to ``findings`` (same length, same order) whose
    entries are the GitLab discussion ``id`` (str) 当 create 成功；否则
    None（无 line、创建失败、无 id 字段）。调用方 :meth:`_persist_review`
    用这个列表把 ``gitlab_discussion_id`` 写回 finding 行，让下一轮改动
    文件时能定向 resolve。
    """

    diff_refs = _extract_diff_refs(changes_payload, event)
    discussion_ids: list[str | None] = []
    for finding in findings:
        if finding.line_number is None:
            discussion_ids.append(None)
            continue
        old_path, new_path = _resolve_finding_paths(changes_payload, finding.file_path)

        # MR 重开 / force push 后旧 finding 的行号可能已失效，
        # 不在当前 diff 有效范围内 → 降级成全局评论，避免贴到错误行
        is_valid_line = _is_line_number_valid_for_current_diff(
            changes_payload,
            new_path,
            finding.line_number,
        )
        if not is_valid_line:
            logger.warning(
                "finding line_number out of current diff range; skipping line-level discussion",
                extra={
                    "project_id": event.project_id,
                    "mr_iid": event.mr_iid,
                    "file_path": finding.file_path,
                    "line_number": finding.line_number,
                },
            )
            discussion_ids.append(None)
            continue

        try:
            response = await gitlab_client.create_merge_request_discussion(
                project_id=event.project_id,
                mr_iid=event.mr_iid,
                body=build_finding_discussion_body(finding),
                base_sha=diff_refs["base_sha"],
                start_sha=diff_refs["start_sha"],
                head_sha=diff_refs["head_sha"],
                old_path=old_path,
                new_path=new_path,
                line_number=finding.line_number,
            )
        except Exception:
            logger.exception(
                "failed to create GitLab MR discussion",
                extra={
                    "project_id": event.project_id,
                    "mr_iid": event.mr_iid,
                    "file_path": finding.file_path,
                    "line_number": finding.line_number,
                },
            )
            discussion_ids.append(None)
            continue
        raw_id = response.get("id") if isinstance(response, dict) else None
        discussion_ids.append(str(raw_id) if raw_id is not None else None)
    return discussion_ids


async def _post_commit_finding_comments(
    event: _CommitLikeEvent,
    changes_payload: dict[str, Any],
    findings: Sequence[Finding],
    *,
    gitlab_client: GitLabClient,
) -> list[str | None]:
    """每个 finding 发一条行级锚定的 GitLab commit 评论。

    Best-effort：单个失败 logger.exception 继续，不阻断汇总评论与
    commit status。行号失效（不在当前 diff 有效范围）/ 无行号 -> 不发
    锚定评论，对应位置记 None。

    Returns:
        与 ``findings`` 同序同长的 comment id（str）列表；未发出为 None。
    """

    comment_ids: list[str | None] = []
    for finding in findings:
        if finding.line_number is None:
            comment_ids.append(None)
            continue
        _, new_path = _resolve_finding_paths(changes_payload, finding.file_path)
        if not _is_line_number_valid_for_current_diff(
            changes_payload,
            new_path,
            finding.line_number,
        ):
            logger.warning(
                "finding line_number out of commit diff range; skipping line-level comment",
                extra={
                    "project_id": event.project_id,
                    "commit_sha": event.commit_sha,
                    "file_path": finding.file_path,
                    "line_number": finding.line_number,
                },
            )
            comment_ids.append(None)
            continue
        try:
            response = await gitlab_client.create_commit_comment(
                project_id=event.project_id,
                sha=event.commit_sha,
                note=build_finding_discussion_body(finding),
                path=new_path,
                line=finding.line_number,
                line_type="new",
            )
        except Exception:
            logger.exception(
                "failed to create GitLab commit comment",
                extra={
                    "project_id": event.project_id,
                    "commit_sha": event.commit_sha,
                    "file_path": finding.file_path,
                    "line_number": finding.line_number,
                },
            )
            comment_ids.append(None)
            continue
        raw_id = response.get("id") if isinstance(response, dict) else None
        comment_ids.append(str(raw_id) if raw_id is not None else None)
    return comment_ids


async def _resolve_stale_discussions_for_files(
    *,
    event: GitLabMergeRequestEvent,
    findings_to_resolve: Sequence[FindingRow],
    gitlab_client: GitLabClient,
) -> None:
    """把本轮改动文件里的历史 open findings 对应的 GitLab discussion 逐条 resolve。

    Best-effort：单条 API 抛异常仅 warning，不影响其它 finding 与主流程。
    ``gitlab_discussion_id`` 为空的（老数据 / 创建 discussion 时曾失败）
    直接跳过 —— 接受"历史 discussion 关不掉"这个已知不完美。

    DB 层的 ``status='resolved'`` 由 :meth:`_persist_review` 在同事务里
    统一处理，本函数只关心 GitLab 侧动作。
    """

    if not findings_to_resolve:
        return
    for row in findings_to_resolve:
        discussion_id = row.gitlab_discussion_id
        if not discussion_id:
            # 老数据没记 discussion_id，或者当初 create 失败。
            continue
        try:
            await gitlab_client.resolve_discussion(
                project_id=event.project_id,
                mr_iid=event.mr_iid,
                discussion_id=discussion_id,
                resolved=True,
            )
        except Exception:
            logger.warning(
                "failed to resolve stale GitLab discussion; continuing",
                extra={
                    "gitlab_project_id": event.project_id,
                    "mr_iid": event.mr_iid,
                    "discussion_id": discussion_id,
                    "finding_id": str(row.id),
                },
            )
