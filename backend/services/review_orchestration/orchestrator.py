"""ReviewOrchestrator: coordinate GitLab diff retrieval, engine execution, and feedback."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from core.block_policy import (
    BlockPolicyLike,
    build_default_block_policies,
    compute_has_blocker,
    match_block_policy,
)
from core.diff_filter import DiffFilterConfig
from core.summary_builder import build_review_summary_note
from engines import ReviewContext
from engines.registry import EngineRegistry, get_engine_registry
from integrations.gitlab.client import GitLabClient
from services.notification_service import NotificationService
from services.repo_reader import GitLabRepoReader
from services.review_orchestration.context_builder import (
    _resolve_history,
    _resolve_provider,
    resolve_rules,
)
from services.review_orchestration.diff_utils import (
    _extract_int,
    build_diff_hunks,
)
from services.review_orchestration.engine_error import (
    _handle_engine_error,
)
from services.review_orchestration.events import (
    GitLabCommitEvent,
    GitLabMergeRequestEvent,
    GitLabPushEvent,
)
from services.review_orchestration.gitlab_feedback import (
    _build_review_detail_url,
    _post_finding_discussions,
    _resolve_stale_discussions_for_files,
)
from services.review_orchestration.handlers import (
    CommitReviewHandler,
    PushReviewHandler,
)
from services.review_orchestration.notification import (
    _push_review_notification,
)
from services.review_orchestration.persistence import (
    _handle_mr_closed,
    _handle_mr_merged,
    _merge_findings_for_plan,
    _persist_review,
    _reopen_mr_closed_findings,
)
from services.review_orchestration.planning import (
    _fetch_changes_for_plan,
    _handle_reuse,
    _plan_review,
)
from services.review_orchestration.results import (
    CommitReviewResult,
    OrchestratorResult,
    _ReviewPlan,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ReviewOrchestrator:
    """Coordinate GitLab diff retrieval, engine execution, and GitLab feedback."""

    def __init__(
        self,
        *,
        gitlab_client: GitLabClient,
        engine_registry: EngineRegistry | None = None,
        default_engine: str = "llm-direct",
        block_policies: Sequence[BlockPolicyLike] | None = None,
        ignore_paths: Sequence[str] | None = None,
        max_diff_bytes: int = 200_000,
        review_detail_base_url: str | None = None,
        session_factory: SessionFactory | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self._gitlab_client = gitlab_client
        self._engine_registry = engine_registry or get_engine_registry()
        self._default_engine = default_engine
        self._block_policies = block_policies
        self._diff_filter_config = DiffFilterConfig(
            ignore_paths=tuple(ignore_paths or ()),
            max_diff_bytes=max_diff_bytes,
        )
        self._review_detail_base_url = (
            review_detail_base_url.rstrip("/") if review_detail_base_url else None
        )
        # session_factory 为 None 时跳过持久化，与旧 MVP 行为保持一致；
        # 传入 async_sessionmaker（或任何返回 AsyncSession 上下文管理器的可调用）时
        # 每次评审会尝试落库 reviews + review_findings。
        self._session_factory = session_factory
        # 通知推送服务（best-effort）。为 None 时跳过推送，保持旧 MVP 行为，便于
        # 不需要通知能力的测试与旧调用方。
        self._notification_service = notification_service
        # commit / push 审查走模板方法 handler（PR2）：公共管线在
        # ReviewCommitStyleHandler 基类，两条链路只注入差异钩子。构造一次
        # 复用，避免每次调用重建对象。
        handler_kwargs: dict[str, Any] = {
            "gitlab_client": gitlab_client,
            "engine_registry": self._engine_registry,
            "default_engine": self._default_engine,
            "block_policies": self._block_policies,
            "diff_filter_config": self._diff_filter_config,
            "review_detail_base_url": self._review_detail_base_url,
            "session_factory": self._session_factory,
            "notification_service": self._notification_service,
        }
        self._commit_handler = CommitReviewHandler(**handler_kwargs)
        self._push_handler = PushReviewHandler(**handler_kwargs)

    async def review_merge_request(self, event: GitLabMergeRequestEvent) -> OrchestratorResult:
        """Run the configured review engine for one GitLab MR event.

        Args:
            event: Normalized merge request event.

        Returns:
            OrchestratorResult: Aggregate execution summary.
        """

        # MR 生命周期事件（close / merge / reopen）不跑 engine，只联动 finding 状态：
        #  - close：把 (project, mr) 所有 open finding 批量标 mr_closed；
        #  - merge：批量标 resolved（视为"跟着代码合进主线"）；
        #  - reopen：把 mr_closed 的 finding 翻回 open，然后走常规增量流程。
        # 前两种直接短路返回；reopen 只做翻转，接下来的常规流程会继续跑。
        if event.action == "close":
            return await _handle_mr_closed(event, session_factory=self._session_factory)
        if event.action == "merge":
            return await _handle_mr_merged(event, session_factory=self._session_factory)
        if event.action == "reopen":
            await _reopen_mr_closed_findings(event, session_factory=self._session_factory)
            # fall through 到常规审查流程

        started_at = time.perf_counter()
        review_id = uuid4()
        block_policy = match_block_policy(
            self._block_policies or build_default_block_policies(event.project_uuid),
            event.target_branch,
        )
        policy_applied = f"{block_policy.branch_pattern} -> {block_policy.block_severity}"
        logger.info("Applying policy", extra={"policy_applied": policy_applied})

        # 按 (project, mr_iid) 决定这次是全量 / 增量 / 复用。
        plan = await _plan_review(
            event,
            session_factory=self._session_factory,
            gitlab_client=self._gitlab_client,
        )
        logger.info(
            "review plan resolved",
            extra={
                "gitlab_project_id": event.project_id,
                "mr_iid": event.mr_iid,
                "mode": plan.mode,
                "base_sha": plan.base_sha,
                "parent_review_id": str(plan.parent_review_id) if plan.parent_review_id else None,
                "reason": plan.reason,
            },
        )

        if plan.mode == "reuse":
            logger.info("handle reuse")
            reuse_result = await _handle_reuse(
                event=event,
                plan=plan,
                policy_applied=policy_applied,
                block_policy=block_policy,
                session_factory=self._session_factory,
                gitlab_client=self._gitlab_client,
                review_detail_base_url=self._review_detail_base_url,
            )
            if reuse_result is not None:
                return reuse_result
            # reuse 失败（比如上次 review 已经不在 DB 里）就降级到 full，继续往下走。
            plan = _ReviewPlan(
                mode="full",
                base_sha=event.target_commit_sha,
                parent_review_id=plan.parent_review_id,
                reason="reuse_failed_fallback_full",
            )

        changes = await _fetch_changes_for_plan(
            event,
            plan,
            gitlab_client=self._gitlab_client,
        )
        # 顺序敏感：先算 rules，再基于启用 rule 集拉负例（scope=rule/both 需要）。
        rules = await resolve_rules(
            event,
            session_factory=self._session_factory,
        )
        history = await _resolve_history(
            event,
            rules,
            session_factory=self._session_factory,
        )
        repo_reader = GitLabRepoReader(
            self._gitlab_client,
            project_id=event.project_id,
            default_ref=event.source_commit_sha,
        )
        context = ReviewContext(
            review_id=review_id,
            project_id=event.project_uuid,
            mr_iid=str(event.mr_iid),
            source_branch=event.source_branch,
            target_branch=event.target_branch,
            source_commit_sha=event.source_commit_sha,
            target_commit_sha=event.target_commit_sha,
            diff_hunks=build_diff_hunks(changes, self._diff_filter_config),
            provider=await _resolve_provider(
                event,
                session_factory=self._session_factory,
            ),
            rules=rules,
            history=history,
            mr_title=event.title,
            mr_description=event.description,
            extra={
                "gitlab_project_id": event.project_id,
                "gitlab_project_path": event.project_path,
                "merge_request_title": event.title,
                "merge_request_url": event.web_url,
                "merge_request_action": event.action,
                "review_mode": plan.mode,
                "review_base_sha": plan.base_sha,
            },
            repo_reader=repo_reader,
        )
        engine = self._engine_registry.get(self._default_engine)
        try:
            findings = await engine.review(context)
        except Exception as exc:
            logger.exception(
                "review engine failed",
                extra={
                    "project_id": event.project_id,
                    "mr_iid": event.mr_iid,
                    "engine": self._default_engine,
                },
            )
            return await _handle_engine_error(
                event=event,
                review_id=review_id,
                policy_applied=policy_applied,
                block_policy=block_policy,
                error=exc,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                plan=plan,
                gitlab_client=self._gitlab_client,
                review_detail_base_url=self._review_detail_base_url,
                notification_service=self._notification_service,
                default_engine=self._default_engine,
                session_factory=self._session_factory,
            )
        # 增量模式下把新 findings 与历史 open findings 合并，得到本次要展示的集合。
        merge = await _merge_findings_for_plan(
            event,
            plan,
            findings,
            session_factory=self._session_factory,
        )
        combined_findings = merge.combined_findings
        has_blocker, blocker_count = compute_has_blocker(combined_findings, block_policy)

        # 本轮改动文件的历史 discussion 先在 GitLab 侧关掉（best-effort），再落
        # 新 discussion —— 保证同一文件的评论上下文始终对齐当前代码状态。
        # DB 层 status='resolved' 由 _persist_review 事务负责。
        await _resolve_stale_discussions_for_files(
            event=event,
            findings_to_resolve=merge.stale_findings_to_resolve,
            gitlab_client=self._gitlab_client,
        )
        discussion_ids = await _post_finding_discussions(
            event,
            changes,
            findings,
            gitlab_client=self._gitlab_client,
        )
        note = await self._gitlab_client.create_merge_request_note(
            project_id=event.project_id,
            mr_iid=event.mr_iid,
            body=build_review_summary_note(
                review_id=review_id,
                findings=combined_findings,
                has_blocker=has_blocker,
                blocker_count=blocker_count,
                policy_applied=policy_applied,
                detail_url=_build_review_detail_url(
                    review_detail_base_url=self._review_detail_base_url,
                    review_id=review_id,
                ),
                review_mode=plan.mode,
                incremental_base_sha=plan.base_sha if plan.mode == "incremental" else None,
                incremental_head_sha=(
                    event.source_commit_sha if plan.mode == "incremental" else None
                ),
                new_finding_count=len(merge.new_findings),
                carried_finding_count=len(merge.carried_over_untouched),
                mode_reason=plan.reason,
                new_findings=list(merge.new_findings),
                carried_findings=list(merge.carried_over_untouched),
            ),
        )
        await self._gitlab_client.set_commit_status(
            project_id=event.project_id,
            commit_sha=event.source_commit_sha,
            state="failed" if has_blocker else "success",
            name="ai-code-reviewer",
            description=(
                f"{len(combined_findings)} finding(s), {blocker_count} blocking finding(s)"
                if has_blocker
                else f"AI Review completed with {len(combined_findings)} finding(s)"
            ),
            target_url=_build_review_detail_url(
                review_detail_base_url=self._review_detail_base_url,
                review_id=review_id,
            ),
        )
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        # 尝试落库；失败不影响主流程返回值。
        await _persist_review(
            event=event,
            review_id=review_id,
            findings=findings,
            has_blocker=has_blocker,
            status_value="done",
            duration_ms=duration_ms,
            engine_used=self._default_engine,
            plan=plan,
            merge=merge,
            combined_finding_count=len(combined_findings),
            discussion_ids=discussion_ids,
            session_factory=self._session_factory,
        )
        # 推送通知（best-effort，失败不影响主流程）。
        await _push_review_notification(
            event=event,
            review_id=review_id,
            finding_count=len(combined_findings),
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            status_value="done",
            findings=combined_findings,
            plan=plan,
            notification_service=self._notification_service,
            review_detail_base_url=self._review_detail_base_url,
        )
        return OrchestratorResult(
            review_id=review_id,
            project_uuid=event.project_uuid,
            status="done",
            finding_count=len(combined_findings),
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            policy_applied=policy_applied,
            note_id=_extract_int(note, "id"),
        )

    async def review_commit(self, event: GitLabCommitEvent) -> CommitReviewResult:
        """Push Hook 逐 commit 审查入口。

        审查 diff 语义：该 commit vs 其第一个 parent（``parent_ids[0]..commit``）。
        结果写回 GitLab：每个 finding 一条行级锚定 commit 评论 + 一条汇总评论 +
        commit status（有 blocker=failed，否则 success）。审查结果**不落库**
        （只持久化 MR 审查），完成后 best-effort 推送钉钉通知。

        行为规则（管线在 :class:`CommitReviewHandler`，此处只做委托）：
          - 项目级 ``project.commit_review_enabled=False`` -> skipped_disabled
            （查不到 Project--无 DB / 未注册--时回退全局 settings 开关）；
          - merge commit（parent_ids >1）-> skipped_merge_commit；根提交
            （parent_ids 为空）-> skipped_root_commit，均无评论无通知；
          - diff 过滤后为空 -> 0 findings + 汇总评论"无可审查变更" + 通知；
          - engine 异常 -> commit status failed + 审查失败评论 + 通知，
            绝不静默通过。

        Args:
            event: 归一化后的 commit 事件。

        Returns:
            CommitReviewResult: 执行摘要。
        """

        return await self._commit_handler.handle(event)

    async def review_push(self, event: GitLabPushEvent) -> CommitReviewResult:
        """Push Hook 合并审查入口：一次 push 的全部 commit 变更合并后单次审查。

        与 :meth:`review_commit`（逐 commit 审查）的区别：
          - diff 语义：``before..after`` 一次 compare 拉取（新建分支时降级为
            head commit 的 diff），不再逐个 commit 判断 merge / root；
          - 一次 push 只做**一次** LLM 调用，全部 commit message 拼接进上下文；
          - 行级评论 / 汇总评论 / commit status 全部写回 head commit（after SHA）。

        行为规则（管线在 :class:`PushReviewHandler`，此处只做委托）：
          - 项目级 ``project.commit_review_enabled=False`` -> skipped_disabled；
          - compare 失败 / 异常 -> skipped_no_changes（记 warning，不误报失败）；
          - diff 过滤后为空 -> 0 findings + 汇总评论"无可审查变更" + success status；
          - engine 异常 -> commit status failed + 审查失败评论，绝不静默通过。

        Args:
            event: 归一化后的 push 事件。

        Returns:
            CommitReviewResult: 执行摘要。
        """

        return await self._push_handler.handle(event)
