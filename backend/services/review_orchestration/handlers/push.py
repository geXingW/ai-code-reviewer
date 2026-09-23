"""Push Hook 合并审查 handler（差异钩子实现，公共管线见 ``base.py``）。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

from core.summary_builder import build_push_review_note
from engines import DiffHunk, Finding, ProviderConfig, ReviewContext, RuleSpec
from engines.types import ReviewHistoryItem
from services.repo_reader import GitLabRepoReader
from services.review_orchestration.events import GitLabPushEvent
from services.review_orchestration.gitlab_feedback import _build_review_detail_url
from services.review_orchestration.handlers.base import (
    ReviewCommitStyleHandler,
    _FetchOutcome,
)
from services.review_orchestration.planning import _fetch_push_changes

logger = logging.getLogger(__name__)


class PushReviewHandler(ReviewCommitStyleHandler[GitLabPushEvent]):
    """Push Hook 合并审查：一次 push 的全部 commit 变更合并后单次审查。

    与 :class:`CommitReviewHandler`（逐 commit 审查）的区别：
      - diff 语义：``before..after`` 一次 compare 拉取（新建分支时降级为
        head commit 的 diff），不再逐个 commit 判断 merge / root；
      - 一次 push 只做**一次** LLM 调用，全部 commit message 拼接进上下文；
      - 行级评论 / 汇总评论 / commit status 全部写回 head commit（after SHA）。

    行为规则：
      - 项目级 ``project.commit_review_enabled=False`` -> skipped_disabled；
      - compare 失败 / 异常 -> skipped_no_changes（记 warning，不误报失败）；
      - diff 过滤后为空 -> 0 findings + 汇总评论"无可审查变更" + success status；
      - engine 异常 -> commit status failed + 审查失败评论，绝不静默通过。
    """

    async def _fetch_changes(self, event: GitLabPushEvent) -> _FetchOutcome:
        changes = await _fetch_push_changes(event, gitlab_client=self._gitlab_client)
        # compare 失败 / 异常 -> None，上层按 skipped_no_changes 处理（不误报失败）。
        return _FetchOutcome(changes=changes)

    def _build_context(
        self,
        event: GitLabPushEvent,
        *,
        review_id: UUID,
        hunks: list[DiffHunk],
        base_sha: str | None,
        rules: list[RuleSpec],
        provider: ProviderConfig | None,
        history: list[ReviewHistoryItem],
        repo_reader: GitLabRepoReader,
    ) -> ReviewContext:
        return ReviewContext(
            review_id=review_id,
            project_id=event.project_uuid,
            # engine 层已与 MR 解耦：mr_iid 不进 prompt，push 审查传空串。
            mr_iid="",
            source_branch=event.branch,
            target_branch=event.branch,
            source_commit_sha=event.after_sha,
            target_commit_sha=event.before_sha,
            diff_hunks=hunks,
            provider=provider,
            rules=rules,
            history=history,
            mr_title=event.branch,
            mr_description="",
            last_commit_message="\n".join(str(c.get("message") or "") for c in event.commits),
            extra={
                "gitlab_project_id": event.project_id,
                "gitlab_project_path": event.project_path,
                "review_kind": "push",
                "review_base_sha": event.before_sha,
                "push_commits": [
                    {"id": str(c.get("id") or ""), "title": str(c.get("title") or "")}
                    for c in event.commits
                ],
            },
            repo_reader=repo_reader,
        )

    def _build_note(
        self,
        *,
        event: GitLabPushEvent,
        review_id: UUID,
        findings: Sequence[Finding],
        has_blocker: bool,
        blocker_count: int,
        policy_applied: str,
    ) -> str:
        return build_push_review_note(
            review_id=review_id,
            head_sha=event.after_sha,
            branch=event.branch,
            commit_count=len(event.commits),
            commit_titles=[str(c.get("title") or "").strip() for c in event.commits],
            findings=findings,
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            policy_applied=policy_applied,
            detail_url=_build_review_detail_url(
                review_detail_base_url=self._review_detail_base_url,
                review_id=review_id,
            ),
        )

    def _head_sha(self, event: GitLabPushEvent) -> str:
        return event.after_sha

    def _log_engine_failure(self, event: GitLabPushEvent) -> None:
        logger.exception(
            "push review engine failed",
            extra={
                "gitlab_project_id": event.project_id,
                "after_sha": event.after_sha,
                "engine": self._default_engine,
            },
        )
