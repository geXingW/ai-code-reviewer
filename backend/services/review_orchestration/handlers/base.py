"""Commit / push 审查的模板方法编排（PR2 of the orchestration split）。

``review_commit`` 与 ``review_push`` 两条链路约 70% 逐行相同（enabled 检查、
空 diff 短路、rules/provider/history 解析、engine 调用、blocker 计算、行级
评论、汇总 note、commit status、通知），唯一真实差异是**取 diff 的方式**和
**note 构造函数**。本模块用模板方法模式把公共管线抽到
:class:`ReviewCommitStyleHandler` 基类，两个子类只保留差异钩子：

- :class:`CommitReviewHandler`：Push Hook 逐 commit 审查（commit vs 第一个
  parent 的 diff，merge / root commit 跳过）；
- :class:`PushReviewHandler`：Push Hook 合并审查（``before..after`` 一次
  compare 拉取，新建分支降级 head commit diff）。

管线顺序固定在基类 :meth:`ReviewCommitStyleHandler.handle`，子类只通过钩子
注入差异；状态字面量、日志文案、GitLab 写回顺序与拆分前逐字一致。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from core.block_policy import BlockPolicyLike, compute_has_blocker
from core.diff_filter import DiffFilterConfig
from engines import DiffHunk, Finding, ProviderConfig, ReviewContext, RuleSpec
from engines.registry import EngineRegistry
from engines.types import ReviewHistoryItem
from integrations.gitlab.client import GitLabClient
from repositories.project import ProjectRepository
from services.notification_service import NotificationService
from services.repo_reader import GitLabRepoReader
from services.review_orchestration.diff_utils import (
    _extract_int,
    build_diff_hunks,
)
from services.review_orchestration.engine_error import _handle_commit_engine_error
from services.review_orchestration.events import _CommitLikeEvent
from services.review_orchestration.gitlab_feedback import (
    _build_review_detail_url,
    _post_commit_finding_comments,
)
from services.review_orchestration.notification import _push_commit_review_notification
from services.review_orchestration.policy import resolve_policy_or_skip
from services.review_orchestration.resolution import (
    _resolve_history,
    _resolve_provider,
    resolve_rules,
)
from services.review_orchestration.results import CommitReviewResult

if TYPE_CHECKING:
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)

# 事件类型钩子：子类绑定具体事件类型后，继承来的模板方法 / 钩子签名自动收窄。
_EventT = TypeVar("_EventT", bound=_CommitLikeEvent)


@dataclass(frozen=True)
class _FetchOutcome:
    """``_fetch_changes`` 钩子的返回值。

    commit 链路的 merge / root commit 跳过发生在取 diff **之前**，要短路返回
    ``skipped_merge_commit`` / ``skipped_root_commit``；push 链路的 compare
    失败语义是 ``skipped_no_changes``。两个维度拆成两个字段表达：

    Attributes:
        changes: ``{"changes": [...]}`` 形态的 GitLab 变更载荷；``None`` 表示
            "无变更"，由模板方法短路成 ``skipped_no_changes``。
        skip_status: 非 None 时模板方法直接以此状态短路返回（merge / root
            commit），优先级高于 ``changes``。
        base_sha: 本次 diff 的起点 SHA（commit 链路为第一个 parent）。commit
            链路在 ``get_commit`` 判断 parents 时顺手拿到，随载荷带回给
            ``_build_context``，避免 handler 用实例字段在两次 await 之间传值
            （并发复用同一 handler 实例时会串值）。
    """

    changes: dict[str, Any] | None
    skip_status: str | None = None
    base_sha: str | None = None


class ReviewCommitStyleHandler(ABC, Generic[_EventT]):
    """commit / push 审查的公共模板方法基类。

    固定管线顺序（子类不许重写 :meth:`handle` 改流程），差异通过抽象钩子注入：

    1. ``_resolve_commit_review_enabled`` -- 项目级开关，关 -> skipped_disabled；
    2. block policy 匹配 -- 未命中任何策略（如 feature 分支）-> skipped_no_policy；
    3. ``_fetch_changes`` -- 取 diff（抽象钩子），merge/root 跳过或空 -> 短路；
    4. ``build_diff_hunks`` -- diff 过滤；
    5. 空 diff 短路（汇总评论 + success status + 通知，顺序固定）；
    6. rules / provider / history 解析 + repo_reader；
    7. ``_build_context`` -- ReviewContext 构建（抽象钩子）；
    8. engine 调用，异常 -> ``_handle_commit_engine_error``（日志文案按链路区分）；
    9. ``compute_has_blocker``；
    10. ``_post_commit_finding_comments`` -- 行级评论；
    11. ``_build_note`` -- 汇总评论（抽象钩子，空 diff 短路也复用）；
    12. ``set_commit_status``；
    13. 通知；
    14. 返回 :class:`CommitReviewResult`。

    共享依赖与 :class:`~services.review_orchestration.orchestrator.ReviewOrchestrator`
    相同，经构造函数注入。
    """

    def __init__(
        self,
        *,
        gitlab_client: GitLabClient,
        engine_registry: EngineRegistry,
        default_engine: str,
        block_policies: Sequence[BlockPolicyLike] | None,
        diff_filter_config: DiffFilterConfig,
        review_detail_base_url: str | None,
        session_factory: SessionFactory | None,
        notification_service: NotificationService | None,
    ) -> None:
        self._gitlab_client = gitlab_client
        self._engine_registry = engine_registry
        self._default_engine = default_engine
        self._block_policies = block_policies
        self._diff_filter_config = diff_filter_config
        self._review_detail_base_url = review_detail_base_url
        self._session_factory = session_factory
        self._notification_service = notification_service

    # -- 模板方法：固定管线顺序，子类不许重写 -------------------------------

    async def handle(self, event: _EventT) -> CommitReviewResult:
        """Run the configured review engine for one commit-style event."""

        if not await self._resolve_commit_review_enabled(event):
            return CommitReviewResult(
                review_id=None,
                project_uuid=event.project_uuid,
                status="skipped_disabled",
            )

        # 策略匹配在取 diff 之前：未命中任何 block policy 的分支（如 feature/*）
        # 直接跳过，不白拉一次 compare / commit diff。
        matched = resolve_policy_or_skip(
            block_policies=self._block_policies,
            project_uuid=event.project_uuid,
            project_id=event.project_id,
            branch=event.branch,
        )
        if matched is None:
            return CommitReviewResult(
                review_id=None,
                project_uuid=event.project_uuid,
                status="skipped_no_policy",
            )
        block_policy, policy_applied = matched

        outcome = await self._fetch_changes(event)
        if outcome.skip_status is not None:
            return CommitReviewResult(
                review_id=None,
                project_uuid=event.project_uuid,
                status=outcome.skip_status,
            )
        changes = outcome.changes
        if changes is None:
            return CommitReviewResult(
                review_id=None,
                project_uuid=event.project_uuid,
                status="skipped_no_changes",
            )
        hunks = build_diff_hunks(changes, self._diff_filter_config)

        if not hunks:
            # 空提交 / 全部被 ignore_paths 过滤 -> 0 findings + 汇总评论 + 通知。
            return await self._finish_empty_diff(event, policy_applied)

        # 顺序敏感：先算 rules，再基于启用 rule 集拉负例（scope=rule/both 需要）。
        rules = await resolve_rules(
            event,
            session_factory=self._session_factory,
        )
        provider = await _resolve_provider(
            event,
            session_factory=self._session_factory,
        )
        history = await _resolve_history(
            event,
            rules,
            session_factory=self._session_factory,
        )
        review_id = uuid4()
        repo_reader = GitLabRepoReader(
            self._gitlab_client,
            project_id=event.project_id,
            default_ref=self._head_sha(event),
        )
        context = self._build_context(
            event,
            review_id=review_id,
            hunks=hunks,
            base_sha=outcome.base_sha,
            rules=rules,
            provider=provider,
            history=history,
            repo_reader=repo_reader,
        )
        engine = self._engine_registry.get(self._default_engine)
        try:
            findings = await engine.review(context)
        except Exception as exc:
            self._log_engine_failure(event)
            return await _handle_commit_engine_error(
                event=event,
                review_id=review_id,
                policy_applied=policy_applied,
                block_policy=block_policy,
                error=exc,
                gitlab_client=self._gitlab_client,
                review_detail_base_url=self._review_detail_base_url,
                notification_service=self._notification_service,
            )

        has_blocker, blocker_count = compute_has_blocker(findings, block_policy)
        await _post_commit_finding_comments(
            event,
            changes,
            findings,
            gitlab_client=self._gitlab_client,
        )
        note = await self._gitlab_client.create_commit_comment(
            project_id=event.project_id,
            sha=self._head_sha(event),
            note=self._build_note(
                event=event,
                review_id=review_id,
                findings=findings,
                has_blocker=has_blocker,
                blocker_count=blocker_count,
                policy_applied=policy_applied,
            ),
        )
        await self._gitlab_client.set_commit_status(
            project_id=event.project_id,
            commit_sha=self._head_sha(event),
            state="failed" if has_blocker else "success",
            name="ai-code-reviewer",
            description=(
                f"{len(findings)} finding(s), {blocker_count} blocking finding(s)"
                if has_blocker
                else f"AI Review completed with {len(findings)} finding(s)"
            ),
            target_url=_build_review_detail_url(
                review_detail_base_url=self._review_detail_base_url,
                review_id=review_id,
            ),
        )
        await _push_commit_review_notification(
            event=event,
            review_id=review_id,
            finding_count=len(findings),
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            status_value="done",
            findings=findings,
            notification_service=self._notification_service,
            review_detail_base_url=self._review_detail_base_url,
        )
        return CommitReviewResult(
            review_id=review_id,
            project_uuid=event.project_uuid,
            status="done",
            finding_count=len(findings),
            has_blocker=has_blocker,
            note_id=_extract_int(note, "id"),
        )

    async def _finish_empty_diff(
        self,
        event: _EventT,
        policy_applied: str,
    ) -> CommitReviewResult:
        """空 diff 短路：汇总评论 -> success status -> 通知 -> done/0。

        顺序敏感（测试断言了调用顺序）：评论 -> status -> 通知 -> return。
        note 走与正常完成相同的 ``_build_note`` 钩子（``findings=[]``），
        保证两条链路的"无可审查变更"模板差异只写一次。
        """

        review_id = uuid4()
        note = await self._gitlab_client.create_commit_comment(
            project_id=event.project_id,
            sha=self._head_sha(event),
            note=self._build_note(
                event=event,
                review_id=review_id,
                findings=[],
                has_blocker=False,
                blocker_count=0,
                policy_applied=policy_applied,
            ),
        )
        await self._gitlab_client.set_commit_status(
            project_id=event.project_id,
            commit_sha=self._head_sha(event),
            state="success",
            name="ai-code-reviewer",
            description="AI Review completed with 0 finding(s)",
            target_url=_build_review_detail_url(
                review_detail_base_url=self._review_detail_base_url,
                review_id=review_id,
            ),
        )
        await _push_commit_review_notification(
            event=event,
            review_id=review_id,
            finding_count=0,
            has_blocker=False,
            blocker_count=0,
            status_value="done",
            findings=[],
            notification_service=self._notification_service,
            review_detail_base_url=self._review_detail_base_url,
        )
        return CommitReviewResult(
            review_id=review_id,
            project_uuid=event.project_uuid,
            status="done",
            finding_count=0,
            has_blocker=False,
            note_id=_extract_int(note, "id"),
        )

    async def _resolve_commit_review_enabled(self, event: _EventT) -> bool:
        """解析 commit 审查的项目级开关。

        优先取 ``project.commit_review_enabled``（项目级隔离）；查不到 Project
        --无 session_factory（MVP 兼容路径）、项目未注册、DB 异常--时回退
        全局 ``settings.commit_review_enabled``，保持旧调用方的行为不变。

        get_settings 经旧模块路径 ``services.review_orchestrator`` 解析：该路径
        是既有测试 monkeypatch 的锚点，运行时与 ``core.config.get_settings``
        是同一个函数对象，行为不变。
        """
        from services import review_orchestrator as _legacy_module

        get_settings_compat = _legacy_module.get_settings
        if self._session_factory is None:
            return get_settings_compat().commit_review_enabled
        try:
            async with self._session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
        except SQLAlchemyError:
            logger.exception(
                "commit review project flag resolution failed; falling back to global settings",
                extra={"gitlab_project_id": event.project_id},
            )
            return get_settings_compat().commit_review_enabled
        if project is None:
            return get_settings_compat().commit_review_enabled
        return bool(project.commit_review_enabled)

    # -- 差异钩子：子类只实现这些 -------------------------------------------

    @abstractmethod
    async def _fetch_changes(self, event: _EventT) -> _FetchOutcome:
        """取本次审查的 GitLab 变更载荷（含 merge/root 短路与无变更语义）。"""

    @abstractmethod
    def _build_context(
        self,
        event: _EventT,
        *,
        review_id: UUID,
        hunks: list[DiffHunk],
        base_sha: str | None,
        rules: list[RuleSpec],
        provider: ProviderConfig | None,
        history: list[ReviewHistoryItem],
        repo_reader: GitLabRepoReader,
    ) -> ReviewContext:
        """构造引擎评审上下文（各链路的字段差异都在这里）。"""

    @abstractmethod
    def _build_note(
        self,
        *,
        event: _EventT,
        review_id: UUID,
        findings: Sequence[Finding],
        has_blocker: bool,
        blocker_count: int,
        policy_applied: str,
    ) -> str:
        """渲染汇总评论正文（空 diff 短路与正常完成共用）。"""

    @abstractmethod
    def _head_sha(self, event: _EventT) -> str:
        """行级评论 / 汇总评论 / commit status / repo_reader 的写回目标 SHA。"""

    @abstractmethod
    def _log_engine_failure(self, event: _EventT) -> None:
        """engine 异常时的 ``logger.exception``（两条链路文案不同，不许合并）。"""
