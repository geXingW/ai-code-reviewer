"""Push Hook 逐 commit 审查 handler（差异钩子实现，公共管线见 ``base.py``）。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

from core.summary_builder import build_commit_review_note
from engines import DiffHunk, Finding, ProviderConfig, ReviewContext, RuleSpec
from engines.types import ReviewHistoryItem
from services.repo_reader import GitLabRepoReader
from services.review_orchestration.events import GitLabCommitEvent
from services.review_orchestration.gitlab_feedback import _build_review_detail_url
from services.review_orchestration.handlers.base import (
    ReviewCommitStyleHandler,
    _FetchOutcome,
)

logger = logging.getLogger(__name__)


class CommitReviewHandler(ReviewCommitStyleHandler[GitLabCommitEvent]):
    """Push Hook 逐 commit 审查：commit vs 其第一个 parent 的 diff。

    审查结果写回 GitLab（行级评论 + 汇总评论 + commit status），**不落库**
    （只持久化 MR 审查），完成后 best-effort 推送钉钉通知。

    行为规则：
      - 项目级 ``project.commit_review_enabled=False`` -> skipped_disabled
        （查不到 Project--无 DB / 未注册--时回退全局 settings 开关）；
      - merge commit（parent_ids >1）-> skipped_merge_commit；根提交
        （parent_ids 为空）-> skipped_root_commit，均无评论无通知；
      - diff 过滤后为空 -> 0 findings + 汇总评论"无可审查变更" + 通知；
      - engine 异常 -> commit status failed + 审查失败评论 + 通知，
        绝不静默通过。
    """

    async def _fetch_changes(self, event: GitLabCommitEvent) -> _FetchOutcome:
        # commit 审查不落库，也就没有"已审查过"记录可查 -- 每次 push 都重新审查。
        # Push Hook payload 不带 parents 信息，必须逐个调 commit 详情 API 判断。
        commit = await self._gitlab_client.get_commit(
            project_id=event.project_id,
            sha=event.commit_sha,
        )
        parent_ids = commit.get("parent_ids")
        parent_id_list = [str(p) for p in parent_ids] if isinstance(parent_ids, list) else []
        if len(parent_id_list) > 1:
            return _FetchOutcome(changes=None, skip_status="skipped_merge_commit")
        if not parent_id_list:
            return _FetchOutcome(changes=None, skip_status="skipped_root_commit")
        parent_sha = parent_id_list[0]

        diffs = await self._gitlab_client.get_commit_diff(
            project_id=event.project_id,
            sha=event.commit_sha,
        )
        return _FetchOutcome(changes={"changes": diffs}, base_sha=parent_sha)

    def _build_context(
        self,
        event: GitLabCommitEvent,
        *,
        review_id: UUID,
        hunks: list[DiffHunk],
        base_sha: str | None,
        rules: list[RuleSpec],
        provider: ProviderConfig | None,
        history: list[ReviewHistoryItem],
        repo_reader: GitLabRepoReader,
    ) -> ReviewContext:
        # base_sha 由 _fetch_changes 判断 parents 时带回，恒非 None。
        parent_sha = base_sha or ""
        return ReviewContext(
            review_id=review_id,
            project_id=event.project_uuid,
            # engine 层已与 MR 解耦：mr_iid 不进 prompt，commit 审查传空串。
            mr_iid="",
            source_branch=event.branch,
            target_branch=event.branch,
            source_commit_sha=event.commit_sha,
            target_commit_sha=parent_sha,
            diff_hunks=hunks,
            provider=provider,
            rules=rules,
            history=history,
            mr_title=event.title,
            mr_description="",
            last_commit_message=event.message,
            extra={
                "gitlab_project_id": event.project_id,
                "gitlab_project_path": event.project_path,
                "review_kind": "commit",
                "review_base_sha": parent_sha,
            },
            repo_reader=repo_reader,
        )

    def _build_note(
        self,
        *,
        event: GitLabCommitEvent,
        review_id: UUID,
        findings: Sequence[Finding],
        has_blocker: bool,
        blocker_count: int,
        policy_applied: str,
    ) -> str:
        return build_commit_review_note(
            review_id=review_id,
            commit_sha=event.commit_sha,
            commit_title=event.title,
            findings=findings,
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            policy_applied=policy_applied,
            detail_url=_build_review_detail_url(
                review_detail_base_url=self._review_detail_base_url,
                review_id=review_id,
            ),
        )

    def _head_sha(self, event: GitLabCommitEvent) -> str:
        return event.commit_sha

    def _log_engine_failure(self, event: GitLabCommitEvent) -> None:
        logger.exception(
            "commit review engine failed",
            extra={
                "gitlab_project_id": event.project_id,
                "commit_sha": event.commit_sha,
                "engine": self._default_engine,
            },
        )
