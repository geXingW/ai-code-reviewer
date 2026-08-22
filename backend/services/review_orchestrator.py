"""Review orchestration from GitLab merge request events to engine execution.

This module intentionally keeps persistence optional for the MVP. It constructs the
runtime :class:`engines.types.ReviewContext`, runs the selected engine, writes
an aggregate MR note, and updates GitLab commit status. A later repository layer
can persist ``reviews`` / ``review_findings`` without changing this public flow.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.block_policy import (
    BlockPolicyLike,
    build_default_block_policies,
    compute_has_blocker,
    compute_has_blocker_for_engine_error,
    match_block_policy,
)
from core.config import get_settings
from core.diff_filter import DiffFilterConfig, filter_gitlab_changes
from core.summary_builder import (
    build_commit_review_note,
    build_finding_discussion_body,
    build_push_review_note,
    build_review_summary_note,
)
from engines import DiffHunk, Finding, ProviderConfig, ReviewContext, RuleSpec
from engines.registry import EngineRegistry, get_engine_registry
from engines.types import ReviewHistoryItem
from integrations.gitlab.client import GitLabClient, GitLabClientError
from models.finding import Finding as FindingRow
from models.negative_example import NegativeExample
from models.review import Review as ReviewRow
from repositories.project import ProjectRepository
from repositories.provider import ProviderRepository
from repositories.review import FindingRepository, ReviewRepository

if TYPE_CHECKING:
    from services.notification_service import NotificationService

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_DIFF_HEADER_RE = re.compile(
    r"@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@",
)
logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class GitLabMergeRequestEvent:
    """Normalized GitLab merge request webhook event.

    Attributes:
        project_id: Numeric GitLab project ID.
        project_path: Namespace-qualified GitLab project path.
        mr_iid: Merge request IID scoped to the project.
        source_branch: Source branch name.
        target_branch: Target branch name.
        source_commit_sha: MR head commit SHA.
        target_commit_sha: Best-known base/default branch commit SHA.
        last_commit_message: MR head 分支最近一次 commit 的 message；来自
            ``object_attributes.last_commit.message``，可能为空（供 LLM 上下文使用）。
        created_at: MR 创建时间（ISO 字符串）；来自 ``object_attributes.created_at``，
            可能为空。
        author_username: MR 创建人（open 事件）/ 触发者的 GitLab 用户名；来自
            webhook 顶层 ``user.username``，缺失时为 ``None``（通知侧不 @ 人）。
        author_name: 同上，取 ``user.name`` 显示名。
    """

    project_id: int
    project_path: str
    mr_iid: int
    source_branch: str
    target_branch: str
    source_commit_sha: str
    target_commit_sha: str
    action: str
    title: str
    web_url: str | None = None
    description: str = ""
    last_commit_message: str = ""
    created_at: str = ""
    author_username: str | None = None
    author_name: str | None = None

    @property
    def project_uuid(self) -> UUID:
        """Return a stable UUID projection for the GitLab project.

        The existing runtime engine contract expects UUID project IDs because the
        database model uses UUID primary keys. Until project lookup is wired in,
        deriving a UUID from the GitLab project ID keeps the context deterministic
        and avoids leaking integer IDs into the engine contract.
        """

        return uuid5(NAMESPACE_URL, f"gitlab-project:{self.project_id}")


@dataclass(frozen=True)
class GitLabCommitEvent:
    """Push Hook 逐 commit 审查用的归一化 commit 事件。

    一个 push payload 携带多个 commit，webhook 层为每个（截断后的）commit
    构造一个本事件交给 :meth:`ReviewOrchestrator.review_commit`。

    Attributes:
        project_id: 数值型 GitLab 项目 ID。
        project_path: 带命名空间的项目路径。
        commit_sha: 该 commit 的 SHA。
        branch: push 目标分支名（``ref`` 去掉 ``refs/heads/`` 前缀）。
        title: commit 标题（首行）。
        message: 完整 commit message。
        author_username: push 触发者的 GitLab 用户名；缺失时 ``None``。
        author_name: 同上，显示名。
    """

    project_id: int
    project_path: str
    commit_sha: str
    branch: str
    title: str
    message: str
    author_username: str | None = None
    author_name: str | None = None

    @property
    def project_uuid(self) -> UUID:
        """与 :class:`GitLabMergeRequestEvent` 相同的 uuid5 派生，保证同一
        GitLab 项目在 MR / commit / push 三条审查链路里映射到同一个内部 UUID。"""

        return uuid5(NAMESPACE_URL, f"gitlab-project:{self.project_id}")


@dataclass(frozen=True)
class GitLabPushEvent:
    """Push Hook 合并审查用的归一化 push 事件。

    一次 push 携带的全部 commit 变更合并后做**单次** LLM 审查，行为规则见
    :meth:`ReviewOrchestrator.review_push`。

    Attributes:
        project_id: 数值型 GitLab 项目 ID。
        project_path: 带命名空间的项目路径。
        branch: push 目标分支名（``ref`` 去掉 ``refs/heads/`` 前缀）。
        before_sha: push 前 ref 指向的 commit SHA（新建分支时为 40 个 0）。
        after_sha: push 后 ref 指向的 commit SHA（head commit）。
        commits: ``[{"id": sha, "title": ..., "message": ...}, ...]``，
            保持 payload 的时间序（旧 -> 新）。
        author_username: push 触发者的 GitLab 用户名；缺失时 ``None``。
        author_name: 同上，显示名。
    """

    project_id: int
    project_path: str
    branch: str
    before_sha: str
    after_sha: str
    commits: list[dict[str, Any]]
    author_username: str | None = None
    author_name: str | None = None

    @property
    def project_uuid(self) -> UUID:
        """与 :class:`GitLabMergeRequestEvent` 相同的 uuid5 派生，保证同一
        GitLab 项目在 MR / commit / push 三条审查链路里映射到同一个内部 UUID。"""

        return uuid5(NAMESPACE_URL, f"gitlab-project:{self.project_id}")

    @property
    def commit_sha(self) -> str:
        """head commit SHA（即 after_sha），合并审查的行级评论 / status 写回目标。

        与 :class:`GitLabCommitEvent.commit_sha` 对齐，让评论、失败反馈、通知
        三个复用方法能用同一个 duck-typing 字段。"""

        return self.after_sha

    @property
    def title(self) -> str:
        """head commit 标题（commits 列表最后一跳，message 首行）。

        供失败反馈与通知兜底展示；commits 为空时返回空串。"""

        for commit in reversed(self.commits):
            title = str(commit.get("title") or "").strip()
            if title:
                return title
        return ""


# provider / rules / history 三个 resolve helper 只依赖 event.project_id，
# MR / commit / push 事件 duck-typing 共用。
_EventLike = GitLabMergeRequestEvent | GitLabCommitEvent | GitLabPushEvent

# commit 级写回（评论 / status / 通知）复用的最小事件形状：都需要
# ``commit_sha``（写回目标）与 ``title``（展示用）两个字段。
_CommitLikeEvent = GitLabCommitEvent | GitLabPushEvent


@dataclass(frozen=True)
class OrchestratorResult:
    """Outcome returned after processing one merge request review."""

    review_id: UUID | None
    project_uuid: UUID
    status: str
    finding_count: int
    has_blocker: bool
    blocker_count: int = 0
    policy_applied: str | None = None
    note_id: int | None = None


@dataclass(frozen=True)
class CommitReviewResult:
    """单个 commit 审查的执行结果。

    Attributes:
        review_id: 本次审查生成的 review ID（仅用于评论 / 通知 / 日志追踪，
            **不落库**）；跳过路径下为 None。
        project_uuid: 项目内部 UUID 投影。
        status: ``done`` / ``engine_error`` / ``skipped_merge_commit`` /
            ``skipped_root_commit`` / ``skipped_disabled``。
        finding_count: engine 产出（或空审时 0）的 finding 数。
        has_blocker: 是否命中阻断策略。
        note_id: 汇总评论的 GitLab comment id。
    """

    review_id: UUID | None
    project_uuid: UUID
    status: str
    finding_count: int = 0
    has_blocker: bool = False
    note_id: int | None = None


@dataclass(frozen=True)
class _ReviewPlan:
    """orchestrator 决策出的本次评审策略。

    分三种模式：

    * ``full``：走 GitLab MR changes 拿完整 base..head diff，是首次审 MR / 无法
      沿用上次结果时的兜底路径。
    * ``incremental``：**只审"本次 push 改动的文件"**（``changed_files``），但
      审查素材是 base..head 完整 diff（过滤后只保留改动文件）——不是 push 增量。
      审完后本轮改动文件的历史 finding + GitLab discussion 会被"整体换代"。
    * ``reuse``：head 未变（同一 commit 重触发），跳过 engine，直接沿用 parent
      review 结果重发 GitLab 反馈。

    Attributes:
        mode: ``"full"`` / ``"incremental"`` / ``"reuse"``。
        base_sha: 本次 diff 起点。full 时为 event.target_commit_sha，incremental
            时为上次 review 的 head（用于日志/note，实际 diff 仍走 base..head），
            reuse 时保留上次 review 的 base（仅用于日志）。
        parent_review_id: 同 MR 上一次已完成 review 的 id；用于串链。
        reason: 供日志说明选中此模式的理由（例如 ``history_rewritten``）。
        changed_files: 本次 push 涉及的文件集合（GitLab compare 里 new_path）。
            **仅 incremental 模式**填值。为 None 表示 non-incremental；空集合
            表示 incremental 但获取失败/过滤后无文件——上层降级 full。
    """

    mode: str
    base_sha: str
    parent_review_id: UUID | None
    reason: str
    changed_files: frozenset[str] | None = None


@dataclass(frozen=True)
class _MergeResult:
    """finding 合并的结果，orchestrator 内部数据结构。

    **新语义（feat/rescan-changed-files）**：改动文件级"整体换代"。

    Attributes:
        combined_findings: 用于 GitLab note / block 判定的合并集合，顺序为
            "本次新增（改动文件）+ carried_over_untouched（未动文件的历史）"。
        new_findings: 本轮 engine 输出（改动文件的全量重审结果），全部当作
            "新增" —— 因为改动文件的老 finding 都会被 resolve 掉。
        carried_over_untouched: 未在本次 push 中涉及的文件的历史 open findings，
            engine.Finding 形态，供 note 展示。DB 中保持原状。
        stale_findings_to_resolve: 本轮改动文件里的历史 open findings（DB 行形态）。
            需要：(a) DB status='resolved' + resolved_in_review_id；
            (b) 对应的 GitLab discussion 调 resolve_discussion。
    """

    combined_findings: list[Finding]
    new_findings: list[Finding]
    carried_over_untouched: list[Finding]
    stale_findings_to_resolve: list[FindingRow]


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
            return await self._handle_mr_closed(event)
        if event.action == "merge":
            return await self._handle_mr_merged(event)
        if event.action == "reopen":
            await self._reopen_mr_closed_findings(event)
            # fall through 到常规审查流程

        started_at = time.perf_counter()
        review_id = uuid4()
        block_policy = match_block_policy(
            self._block_policies or build_default_block_policies(event.project_uuid),
            event.target_branch,
        )
        policy_applied = f"{block_policy.branch_pattern} -> {block_policy.block_severity}"

        # 按 (project, mr_iid) 决定这次是全量 / 增量 / 复用。
        plan = await self._plan_review(event)
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
            reuse_result = await self._handle_reuse(
                event=event,
                plan=plan,
                policy_applied=policy_applied,
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

        changes = await self._fetch_changes_for_plan(event, plan)
        # 顺序敏感：先算 rules，再基于启用 rule 集拉负例（scope=rule/both 需要）。
        rules = await self._resolve_rules(event)
        history = await self._resolve_history(event, rules)
        context = ReviewContext(
            review_id=review_id,
            project_id=event.project_uuid,
            mr_iid=str(event.mr_iid),
            source_branch=event.source_branch,
            target_branch=event.target_branch,
            source_commit_sha=event.source_commit_sha,
            target_commit_sha=event.target_commit_sha,
            diff_hunks=self._build_diff_hunks(changes),
            provider=await self._resolve_provider(event),
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
            return await self._handle_engine_error(
                event=event,
                review_id=review_id,
                policy_applied=policy_applied,
                block_policy=block_policy,
                error=exc,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                plan=plan,
            )
        # 增量模式下把新 findings 与历史 open findings 合并，得到本次要展示的集合。
        merge = await self._merge_findings_for_plan(event, plan, findings)
        combined_findings = merge.combined_findings
        has_blocker, blocker_count = compute_has_blocker(combined_findings, block_policy)

        # 本轮改动文件的历史 discussion 先在 GitLab 侧关掉（best-effort），再落
        # 新 discussion —— 保证同一文件的评论上下文始终对齐当前代码状态。
        # DB 层 status='resolved' 由 _persist_review 事务负责。
        await self._resolve_stale_discussions_for_files(
            event=event,
            findings_to_resolve=merge.stale_findings_to_resolve,
        )
        discussion_ids = await self._post_finding_discussions(event, changes, findings)
        note = await self._gitlab_client.create_merge_request_note(
            project_id=event.project_id,
            mr_iid=event.mr_iid,
            body=build_review_summary_note(
                review_id=review_id,
                findings=combined_findings,
                has_blocker=has_blocker,
                blocker_count=blocker_count,
                policy_applied=policy_applied,
                detail_url=self._build_review_detail_url(review_id),
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
            target_url=self._build_review_detail_url(review_id),
        )
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        # 尝试落库；失败不影响主流程返回值。
        await self._persist_review(
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
        )
        # 推送通知（best-effort，失败不影响主流程）。
        await self._push_review_notification(
            event=event,
            review_id=review_id,
            finding_count=len(combined_findings),
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            status_value="done",
            findings=combined_findings,
            plan=plan,
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

    async def _resolve_commit_review_enabled(self, event: _EventLike) -> bool:
        """解析 commit 审查的项目级开关。

        优先取 ``project.commit_review_enabled``（项目级隔离）；查不到 Project
        --无 session_factory（MVP 兼容路径）、项目未注册、DB 异常--时回退
        全局 ``settings.commit_review_enabled``，保持旧调用方的行为不变。
        """
        if self._session_factory is None:
            return get_settings().commit_review_enabled
        try:
            async with self._session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
        except SQLAlchemyError:
            logger.exception(
                "commit review project flag resolution failed; falling back to global settings",
                extra={"gitlab_project_id": event.project_id},
            )
            return get_settings().commit_review_enabled
        if project is None:
            return get_settings().commit_review_enabled
        return bool(project.commit_review_enabled)

    async def review_commit(self, event: GitLabCommitEvent) -> CommitReviewResult:
        """Push Hook 逐 commit 审查入口。

        审查 diff 语义：该 commit vs 其第一个 parent（``parent_ids[0]..commit``）。
        结果写回 GitLab：每个 finding 一条行级锚定 commit 评论 + 一条汇总评论 +
        commit status（有 blocker=failed，否则 success）。审查结果**不落库**
        （只持久化 MR 审查），完成后 best-effort 推送钉钉通知。

        行为规则：
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

        if not await self._resolve_commit_review_enabled(event):
            return CommitReviewResult(
                review_id=None,
                project_uuid=event.project_uuid,
                status="skipped_disabled",
            )

        # commit 审查不落库，也就没有"已审查过"记录可查 -- 每次 push 都重新审查。
        # Push Hook payload 不带 parents 信息，必须逐个调 commit 详情 API 判断。
        commit = await self._gitlab_client.get_commit(
            project_id=event.project_id,
            sha=event.commit_sha,
        )
        parent_ids = commit.get("parent_ids")
        parent_id_list = [str(p) for p in parent_ids] if isinstance(parent_ids, list) else []
        if len(parent_id_list) > 1:
            return CommitReviewResult(
                review_id=None,
                project_uuid=event.project_uuid,
                status="skipped_merge_commit",
            )
        if not parent_id_list:
            return CommitReviewResult(
                review_id=None,
                project_uuid=event.project_uuid,
                status="skipped_root_commit",
            )
        parent_sha = parent_id_list[0]

        diffs = await self._gitlab_client.get_commit_diff(
            project_id=event.project_id,
            sha=event.commit_sha,
        )
        changes = {"changes": diffs}
        hunks = self._build_diff_hunks(changes)

        block_policy = match_block_policy(
            self._block_policies or build_default_block_policies(event.project_uuid),
            event.branch,
        )
        policy_applied = f"{block_policy.branch_pattern} -> {block_policy.block_severity}"

        if not hunks:
            # 空提交 / 全部被 ignore_paths 过滤 -> 0 findings + 汇总评论 + 通知。
            review_id = uuid4()
            note = await self._gitlab_client.create_commit_comment(
                project_id=event.project_id,
                sha=event.commit_sha,
                note=build_commit_review_note(
                    review_id=review_id,
                    commit_sha=event.commit_sha,
                    commit_title=event.title,
                    findings=[],
                    has_blocker=False,
                    blocker_count=0,
                    policy_applied=policy_applied,
                    detail_url=self._build_review_detail_url(review_id),
                ),
            )
            await self._gitlab_client.set_commit_status(
                project_id=event.project_id,
                commit_sha=event.commit_sha,
                state="success",
                name="ai-code-reviewer",
                description="AI Review completed with 0 finding(s)",
                target_url=self._build_review_detail_url(review_id),
            )
            await self._push_commit_review_notification(
                event=event,
                review_id=review_id,
                finding_count=0,
                has_blocker=False,
                blocker_count=0,
                status_value="done",
                findings=[],
            )
            return CommitReviewResult(
                review_id=review_id,
                project_uuid=event.project_uuid,
                status="done",
                finding_count=0,
                has_blocker=False,
                note_id=_extract_int(note, "id"),
            )

        rules = await self._resolve_rules(event)
        provider = await self._resolve_provider(event)
        history = await self._resolve_history(event, rules)
        review_id = uuid4()
        context = ReviewContext(
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
        )
        engine = self._engine_registry.get(self._default_engine)
        try:
            findings = await engine.review(context)
        except Exception as exc:
            logger.exception(
                "commit review engine failed",
                extra={
                    "gitlab_project_id": event.project_id,
                    "commit_sha": event.commit_sha,
                    "engine": self._default_engine,
                },
            )
            return await self._handle_commit_engine_error(
                event=event,
                review_id=review_id,
                policy_applied=policy_applied,
                block_policy=block_policy,
                error=exc,
            )

        has_blocker, blocker_count = compute_has_blocker(findings, block_policy)
        await self._post_commit_finding_comments(event, changes, findings)
        note = await self._gitlab_client.create_commit_comment(
            project_id=event.project_id,
            sha=event.commit_sha,
            note=build_commit_review_note(
                review_id=review_id,
                commit_sha=event.commit_sha,
                commit_title=event.title,
                findings=findings,
                has_blocker=has_blocker,
                blocker_count=blocker_count,
                policy_applied=policy_applied,
                detail_url=self._build_review_detail_url(review_id),
            ),
        )
        await self._gitlab_client.set_commit_status(
            project_id=event.project_id,
            commit_sha=event.commit_sha,
            state="failed" if has_blocker else "success",
            name="ai-code-reviewer",
            description=(
                f"{len(findings)} finding(s), {blocker_count} blocking finding(s)"
                if has_blocker
                else f"AI Review completed with {len(findings)} finding(s)"
            ),
            target_url=self._build_review_detail_url(review_id),
        )
        await self._push_commit_review_notification(
            event=event,
            review_id=review_id,
            finding_count=len(findings),
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            status_value="done",
            findings=findings,
        )
        return CommitReviewResult(
            review_id=review_id,
            project_uuid=event.project_uuid,
            status="done",
            finding_count=len(findings),
            has_blocker=has_blocker,
            note_id=_extract_int(note, "id"),
        )

    async def review_push(self, event: GitLabPushEvent) -> CommitReviewResult:
        """Push Hook 合并审查入口：一次 push 的全部 commit 变更合并后单次审查。

        与 :meth:`review_commit`（逐 commit 审查）的区别：
          - diff 语义：``before..after`` 一次 compare 拉取（新建分支时降级为
            head commit 的 diff），不再逐个 commit 判断 merge / root；
          - 一次 push 只做**一次** LLM 调用，全部 commit message 拼接进上下文；
          - 行级评论 / 汇总评论 / commit status 全部写回 head commit（after SHA）。

        行为规则：
          - 项目级 ``project.commit_review_enabled=False`` -> skipped_disabled；
          - compare 失败 / 异常 -> skipped_no_changes（记 warning，不误报失败）；
          - diff 过滤后为空 -> 0 findings + 汇总评论"无可审查变更" + success status；
          - engine 异常 -> commit status failed + 审查失败评论，绝不静默通过。

        Args:
            event: 归一化后的 push 事件。

        Returns:
            CommitReviewResult: 执行摘要。
        """

        if not await self._resolve_commit_review_enabled(event):
            return CommitReviewResult(
                review_id=None,
                project_uuid=event.project_uuid,
                status="skipped_disabled",
            )

        changes = await self._fetch_push_changes(event)
        if changes is None:
            return CommitReviewResult(
                review_id=None,
                project_uuid=event.project_uuid,
                status="skipped_no_changes",
            )
        hunks = self._build_diff_hunks(changes)

        block_policy = match_block_policy(
            self._block_policies or build_default_block_policies(event.project_uuid),
            event.branch,
        )
        policy_applied = f"{block_policy.branch_pattern} -> {block_policy.block_severity}"
        commit_titles = [str(c.get("title") or "").strip() for c in event.commits]

        if not hunks:
            # 空 diff / 全部被 ignore_paths 过滤 -> 0 findings + 汇总评论 + success status。
            review_id = uuid4()
            note = await self._gitlab_client.create_commit_comment(
                project_id=event.project_id,
                sha=event.after_sha,
                note=build_push_review_note(
                    review_id=review_id,
                    head_sha=event.after_sha,
                    branch=event.branch,
                    commit_count=len(event.commits),
                    commit_titles=commit_titles,
                    findings=[],
                    has_blocker=False,
                    blocker_count=0,
                    policy_applied=policy_applied,
                    detail_url=self._build_review_detail_url(review_id),
                ),
            )
            await self._gitlab_client.set_commit_status(
                project_id=event.project_id,
                commit_sha=event.after_sha,
                state="success",
                name="ai-code-reviewer",
                description="AI Review completed with 0 finding(s)",
                target_url=self._build_review_detail_url(review_id),
            )
            await self._push_commit_review_notification(
                event=event,
                review_id=review_id,
                finding_count=0,
                has_blocker=False,
                blocker_count=0,
                status_value="done",
                findings=[],
            )
            return CommitReviewResult(
                review_id=review_id,
                project_uuid=event.project_uuid,
                status="done",
                finding_count=0,
                has_blocker=False,
                note_id=_extract_int(note, "id"),
            )

        rules = await self._resolve_rules(event)
        provider = await self._resolve_provider(event)
        history = await self._resolve_history(event, rules)
        review_id = uuid4()
        context = ReviewContext(
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
        )
        engine = self._engine_registry.get(self._default_engine)
        try:
            findings = await engine.review(context)
        except Exception as exc:
            logger.exception(
                "push review engine failed",
                extra={
                    "gitlab_project_id": event.project_id,
                    "after_sha": event.after_sha,
                    "engine": self._default_engine,
                },
            )
            return await self._handle_commit_engine_error(
                event=event,
                review_id=review_id,
                policy_applied=policy_applied,
                block_policy=block_policy,
                error=exc,
            )

        has_blocker, blocker_count = compute_has_blocker(findings, block_policy)
        await self._post_commit_finding_comments(event, changes, findings)
        note = await self._gitlab_client.create_commit_comment(
            project_id=event.project_id,
            sha=event.after_sha,
            note=build_push_review_note(
                review_id=review_id,
                head_sha=event.after_sha,
                branch=event.branch,
                commit_count=len(event.commits),
                commit_titles=commit_titles,
                findings=findings,
                has_blocker=has_blocker,
                blocker_count=blocker_count,
                policy_applied=policy_applied,
                detail_url=self._build_review_detail_url(review_id),
            ),
        )
        await self._gitlab_client.set_commit_status(
            project_id=event.project_id,
            commit_sha=event.after_sha,
            state="failed" if has_blocker else "success",
            name="ai-code-reviewer",
            description=(
                f"{len(findings)} finding(s), {blocker_count} blocking finding(s)"
                if has_blocker
                else f"AI Review completed with {len(findings)} finding(s)"
            ),
            target_url=self._build_review_detail_url(review_id),
        )
        await self._push_commit_review_notification(
            event=event,
            review_id=review_id,
            finding_count=len(findings),
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            status_value="done",
            findings=findings,
        )
        return CommitReviewResult(
            review_id=review_id,
            project_uuid=event.project_uuid,
            status="done",
            finding_count=len(findings),
            has_blocker=has_blocker,
            note_id=_extract_int(note, "id"),
        )

    async def _fetch_push_changes(self, event: GitLabPushEvent) -> dict[str, Any] | None:
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
                diffs = await self._gitlab_client.get_commit_diff(
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
            payload = await self._gitlab_client.compare_refs(
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

    def _build_diff_hunks(self, changes_payload: dict[str, Any]) -> list[DiffHunk]:
        """Convert GitLab ``changes`` payload into filtered engine diff hunks."""

        hunks: list[DiffHunk] = []
        raw_changes = changes_payload.get("changes", [])
        if not isinstance(raw_changes, list):
            return hunks
        for change in filter_gitlab_changes(raw_changes, self._diff_filter_config):
            diff = str(change.get("diff") or "")
            header = _DIFF_HEADER_RE.search(diff)
            hunks.append(
                DiffHunk(
                    file_path=str(change.get("new_path") or change.get("old_path") or "unknown"),
                    old_path=str(change.get("old_path") or "") or None,
                    new_start=_match_int(header, "new_start", default=1),
                    new_lines=_match_int(header, "new_lines", default=1),
                    old_start=_match_int(header, "old_start", default=1),
                    old_lines=_match_int(header, "old_lines", default=1),
                    content=diff,
                    is_new_file=bool(change.get("new_file", False)),
                    is_deleted_file=bool(change.get("deleted_file", False)),
                )
            )
        return hunks

    async def _post_finding_discussions(
        self,
        event: GitLabMergeRequestEvent,
        changes_payload: dict[str, Any],
        findings: Sequence[Finding],
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
                response = await self._gitlab_client.create_merge_request_discussion(
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
        self,
        event: _CommitLikeEvent,
        changes_payload: dict[str, Any],
        findings: Sequence[Finding],
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
                response = await self._gitlab_client.create_commit_comment(
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

    async def _handle_commit_engine_error(
        self,
        *,
        event: _CommitLikeEvent,
        review_id: UUID,
        policy_applied: str,
        block_policy: BlockPolicyLike,
        error: Exception,
    ) -> CommitReviewResult:
        """commit 审查引擎失败的确定性反馈：失败评论 + failed status + 通知。

        与 MR 流的 :meth:`_handle_engine_error` 语义对齐，但 commit 审查没有
        "阻断合并"概念，**commit status 恒为 failed**（失败不装成功），错误细节
        不回显（防泄漏，只写固定文案）。
        """

        has_blocker, blocker_count = compute_has_blocker_for_engine_error(block_policy)
        note = await self._gitlab_client.create_commit_comment(
            project_id=event.project_id,
            sha=event.commit_sha,
            note=build_commit_review_note(
                review_id=review_id,
                commit_sha=event.commit_sha,
                commit_title=event.title,
                findings=[],
                has_blocker=has_blocker,
                blocker_count=blocker_count,
                policy_applied=policy_applied,
                detail_url=self._build_review_detail_url(review_id),
                engine_error="AI Review engine failed before producing findings.",
            ),
        )
        await self._gitlab_client.set_commit_status(
            project_id=event.project_id,
            commit_sha=event.commit_sha,
            state="failed",
            name="ai-code-reviewer",
            description="AI Review engine failed",
            target_url=self._build_review_detail_url(review_id),
        )
        await self._push_commit_review_notification(
            event=event,
            review_id=review_id,
            finding_count=0,
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            status_value="engine_error",
            findings=[],
        )
        return CommitReviewResult(
            review_id=review_id,
            project_uuid=event.project_uuid,
            status="engine_error",
            finding_count=0,
            has_blocker=has_blocker,
            note_id=_extract_int(note, "id"),
        )

    async def _handle_engine_error(
        self,
        *,
        event: GitLabMergeRequestEvent,
        review_id: UUID,
        policy_applied: str,
        block_policy: BlockPolicyLike,
        error: Exception,
        duration_ms: int = 0,
        plan: _ReviewPlan | None = None,
    ) -> OrchestratorResult:
        """Persist deterministic GitLab feedback when the selected engine fails."""

        has_blocker, blocker_count = compute_has_blocker_for_engine_error(block_policy)
        # 引擎失败时用一个"降级"占位 plan：base_sha 兜底到 target_commit_sha，
        # 保证落库时 review_mode / base_sha 仍是合法值。
        effective_plan = plan or _ReviewPlan(
            mode="full",
            base_sha=event.target_commit_sha,
            parent_review_id=None,
            reason="engine_error_no_plan",
        )
        note = await self._gitlab_client.create_merge_request_note(
            project_id=event.project_id,
            mr_iid=event.mr_iid,
            body=build_review_summary_note(
                review_id=review_id,
                findings=[],
                has_blocker=has_blocker,
                blocker_count=blocker_count,
                policy_applied=policy_applied,
                detail_url=self._build_review_detail_url(review_id),
                engine_error="AI Review engine failed before producing findings.",
                review_mode=effective_plan.mode,
                mode_reason=effective_plan.reason,
            ),
        )
        await self._gitlab_client.set_commit_status(
            project_id=event.project_id,
            commit_sha=event.source_commit_sha,
            state="failed" if has_blocker else "success",
            name="ai-code-reviewer",
            description=(
                "AI Review engine failed and policy blocks merge"
                if has_blocker
                else "AI Review engine failed; policy allows merge"
            ),
            target_url=self._build_review_detail_url(review_id),
        )
        # 引擎失败也要落一条 engine_error 记录，方便运营侧统计降级次数。
        await self._persist_review(
            event=event,
            review_id=review_id,
            findings=[],
            has_blocker=has_blocker,
            status_value="engine_error",
            duration_ms=duration_ms,
            engine_used=self._default_engine,
            plan=effective_plan,
            merge=None,
            combined_finding_count=0,
            discussion_ids=None,
        )
        # 推送通知（best-effort，失败不影响主流程）。
        await self._push_review_notification(
            event=event,
            review_id=review_id,
            finding_count=0,
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            status_value="engine_error",
            findings=[],
            plan=effective_plan,
        )
        return OrchestratorResult(
            review_id=review_id,
            project_uuid=event.project_uuid,
            status="engine_error",
            finding_count=0,
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            policy_applied=policy_applied,
            note_id=_extract_int(note, "id"),
        )

    async def _resolve_provider(
        self,
        event: _EventLike,
    ) -> ProviderConfig | None:
        """按 GitLab project_id 查 Project 关联的 Provider，转成 ``ProviderConfig``。

        为 orchestrator 的引擎调用注入 provider 配置。查不到 Project、Project 未
        关联 provider_id、Provider 已删或已禁用、DB / 解密异常，一律返回 ``None``
        让 llm-direct 引擎优雅退化（跳过评审、返回空 findings），**绝不能阻断
        主流程**。

        Args:
            event: 归一化后的 MR 事件。

        Returns:
            解密后的 ``ProviderConfig``；无法解析时 ``None``。
        """

        if self._session_factory is None:
            return None
        try:
            async with self._session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
                if project is None or project.provider_id is None:
                    return None
                provider_repo = ProviderRepository(session)
                provider = await provider_repo.get(project.provider_id)
                if provider is None or not provider.enabled:
                    logger.warning(
                        "provider missing or disabled; llm-direct will skip",
                        extra={
                            "gitlab_project_id": event.project_id,
                            "provider_id": str(project.provider_id),
                        },
                    )
                    return None
                # Provider.api_key 是 EncryptedString，读出时已自动解密。
                return ProviderConfig(
                    provider_id=provider.id,
                    provider_type=provider.protocol,
                    base_url=provider.base_url,
                    model=provider.model,
                    api_key=provider.api_key,
                    temperature=provider.temperature,
                    max_tokens=provider.max_tokens,
                    extra=provider.extra_headers or {},
                )
        except SQLAlchemyError:
            logger.exception(
                "provider resolution failed",
                extra={"gitlab_project_id": event.project_id},
            )
            return None
        except Exception:
            # 解密失败 / Fernet key 不匹配等异常也吞掉，走 llm-direct skip 分支。
            logger.exception(
                "provider resolution failed with unexpected error",
                extra={"gitlab_project_id": event.project_id},
            )
            return None

    async def _resolve_rules(
        self,
        event: _EventLike,
    ) -> list[RuleSpec]:
        """从 DB 查项目已启用的规则并投影为 ``RuleSpec`` 列表。

        走 ``Project.project_rules`` selectin 关系，只保留 ProjectRule.enabled=True
        且底层 Rule.enabled=True 的项；severity 优先取 ProjectRule.severity_override，
        否则用 Rule.severity_default，构造成 ``RuleSpec`` 交给引擎放入 prompt。

        - ``session_factory`` 为 None、Project 未注册、DB 异常：一律返回空列表，
          让引擎走无规则路径（llm-direct 目前会打印 "No project-specific rules
          were supplied. Focus on correctness and security."）。绝不能阻断主流程。

        Args:
            event: 归一化后的 MR 事件。

        Returns:
            投影后的 ``RuleSpec`` 列表；查询失败或无规则时返回空列表。
        """

        if self._session_factory is None:
            return []
        try:
            async with self._session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
                if project is None:
                    return []
                specs: list[RuleSpec] = []
                for link in project.project_rules:
                    if not link.enabled:
                        continue
                    rule = link.rule
                    if rule is None or not rule.enabled:
                        continue
                    severity = link.severity_override or rule.severity_default
                    # 规范化到 Literal["INFO","WARNING","BLOCKER"]；未知值降级为 WARNING
                    severity_upper = severity.upper() if isinstance(severity, str) else "WARNING"
                    if severity_upper not in ("INFO", "WARNING", "BLOCKER"):
                        severity_upper = "WARNING"
                    specs.append(
                        RuleSpec(
                            id=rule.id,
                            rule_id=rule.rule_id,
                            title=rule.title,
                            description=rule.prompt_snippet,
                            severity=severity_upper,
                            # rule.category_default 可能为 None（老数据未回填）；
                            # 让 engine 侧的 _format_rules 用 'other' 兜底以对齐
                            # FindingCategory 枚举，'general' 不在合法值内。
                            category=rule.category_default,
                            enabled=True,
                        )
                    )
                return specs
        except SQLAlchemyError:
            logger.exception(
                "rules resolution failed",
                extra={"gitlab_project_id": event.project_id},
            )
            return []
        except Exception:
            logger.exception(
                "rules resolution failed with unexpected error",
                extra={"gitlab_project_id": event.project_id},
            )
            return []

    async def _resolve_history(
        self,
        event: _EventLike,
        rules: list[RuleSpec],
    ) -> list[ReviewHistoryItem]:
        """从 DB 拉批准过的 NegativeExample 转成 ReviewHistoryItem 列表。

        用于把历史误报"喂"回 prompt，让引擎既能在 prompt 里读到"不要再报这些"，
        又能在 engine 端的 ``_matches_false_positive_history`` 硬过滤阶段兜底
        drop 掉相似 finding。

        - ``settings.llm_history_max_items`` 为 0 → 直接短路返回空列表，甚至
          不打开 DB session。
        - ``session_factory`` 为 None → 保留旧 MVP 行为，返回空列表。
        - Project 未在管理后台注册 → 返回空列表（拿不到 project_id 就不谈范围）。
        - 按 ``settings.llm_history_scope`` 构造 WHERE：
          * ``project``：仅 ``ne.project_id == project.id``（不含 project_id NULL 的全局负例）；
          * ``rule``：仅 ``ne.rule_id IN <当前启用规则 rule_id 集合>``（可拉到任意项目
            及全局负例，只看规则命中）；
          * ``both``：上述两者的 OR 并集，SQL 层通过 id DISTINCT 去重。
        - LEFT OUTER JOIN 兜底：``source_finding_id`` 指向的 finding 可能被 SET NULL，
          此时 file_path / title / description / line_number 都拿不到，
          走 "(unknown)" + 兜底标题让 engine 侧的硬过滤跳过它（file_path 不同不匹配），
          只留 prompt 展示 explanation。
        - 排序：``approved_at DESC, created_at DESC`` —— NULLS LAST 语义在 MySQL/PG
          之间语法不一致，改用两级排序，让"最近批准且落库最晚"的负例排前。
        - DB 任何异常都被 catch，logger.warning + 返回空列表，不阻断主流程。
        """

        settings = get_settings()
        limit = settings.llm_history_max_items
        if limit <= 0:
            # 用户显式关闭反哺；不查 DB，直接短路。
            return []
        if self._session_factory is None:
            return []
        scope = settings.llm_history_scope
        # scope=rule / both 需要"当前启用规则的 rule_id 集合"；scope=project 用不到。
        # 只取 enabled 的规则（_resolve_rules 已经过滤了 enabled=False 的 link 与 rule）。
        active_rule_keys = [rule.rule_id for rule in rules if rule.enabled]

        try:
            async with self._session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
                if project is None:
                    return []

                # 组装 scope 分支的 WHERE 子句。project=only 项目负例；rule=只按规则命中；
                # both=OR 并集。scope=rule 且没有启用规则时不可能命中任何负例，早退。
                where_clauses = []
                if scope in ("project", "both"):
                    where_clauses.append(NegativeExample.project_id == project.id)
                if scope in ("rule", "both"):
                    if active_rule_keys:
                        where_clauses.append(NegativeExample.rule_id.in_(active_rule_keys))
                    elif scope == "rule":
                        # 无启用规则 → 按规则维度什么都拉不到，直接 return。
                        return []
                if not where_clauses:
                    # 极端保险：不该发生，构造保守空结果。
                    return []
                combined_where = (
                    where_clauses[0] if len(where_clauses) == 1 else or_(*where_clauses)
                )

                # LEFT OUTER JOIN 兜底 source finding 已被 SET NULL 的情况。
                stmt = (
                    select(NegativeExample, FindingRow)
                    .select_from(NegativeExample)
                    .outerjoin(FindingRow, FindingRow.id == NegativeExample.source_finding_id)
                    .where(combined_where)
                    # NULLS LAST 用两级排序兼容 MySQL / PG：approved_at 为 NULL 时
                    # 由 created_at 兜底排后面（近期新落库的靠前）。
                    .order_by(
                        NegativeExample.approved_at.desc(),
                        NegativeExample.created_at.desc(),
                    )
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = result.all()

                history: list[ReviewHistoryItem] = []
                seen_ids: set[UUID] = set()
                for ne, finding_row in rows:
                    # scope=both 时 SQL 层通过 OR 可能重复；用 id 去重。
                    if ne.id in seen_ids:
                        continue
                    seen_ids.add(ne.id)
                    if finding_row is not None:
                        file_path = finding_row.file_path
                        line_number = finding_row.line_number
                        title = finding_row.title
                        description = finding_row.description
                    else:
                        # source finding 已被删；file_path 用 "(unknown)" 兜底，
                        # 让 engine 侧硬过滤（rule_id + file_path）跳过它，只走
                        # prompt 展示 explanation。
                        file_path = "(unknown)"
                        line_number = None
                        title = f"Historical false-positive for {ne.rule_id}"
                        description = None
                    confirmed_at = (
                        ne.approved_at.isoformat()
                        if ne.approved_at is not None
                        else ne.created_at.isoformat()
                    )
                    history.append(
                        ReviewHistoryItem(
                            rule_id=ne.rule_id,
                            file_path=file_path,
                            line_number=line_number,
                            title=title,
                            description=description,
                            review_note=(
                                ne.explanation
                                if ne.explanation
                                else "Confirmed false-positive; do not re-report."
                            ),
                            confirmed_at=confirmed_at,
                        )
                    )

                if history:
                    logger.info(
                        "negative examples injected",
                        extra={
                            "count": len(history),
                            "gitlab_project_id": event.project_id,
                            "scope": scope,
                        },
                    )
                else:
                    logger.debug(
                        "no negative examples matched for review",
                        extra={
                            "gitlab_project_id": event.project_id,
                            "scope": scope,
                        },
                    )
                return history
        except Exception:
            # 落库失败不影响主流程：任何 DB / ORM / 校验异常都吞掉，返回空历史。
            logger.warning(
                "failed to resolve history, review continues without negative examples",
                exc_info=True,
                extra={"gitlab_project_id": event.project_id},
            )
            return []

    async def _find_completed_review(
        self,
        event: GitLabMergeRequestEvent,
    ) -> ReviewRow | None:
        """DEPRECATED: 保留供未来诊断脚本使用。

        增量审查引入后主流程不再基于 (project, commit_sha) 全局去重（不同 MR 可能
        引用同一 commit）；同 MR 同 head 的复用改走 :meth:`_plan_review` +
        :meth:`_handle_reuse`。本方法目前**未被主流程调用**，保留只是方便运营
        脚本 / 回滚。
        """

        if self._session_factory is None:
            return None
        try:
            async with self._session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
                if project is None:
                    return None
                review_repo = ReviewRepository(session)
                return await review_repo.find_completed_by_project_and_commit(
                    project.id, event.source_commit_sha,
                )
        except SQLAlchemyError:
            logger.exception(
                "commit_sha dedup lookup failed",
                extra={
                    "gitlab_project_id": event.project_id,
                    "commit_sha": event.source_commit_sha,
                },
            )
            return None

    async def _plan_review(self, event: GitLabMergeRequestEvent) -> _ReviewPlan:
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
        if self._session_factory is None:
            return default_full
        try:
            async with self._session_factory() as session:
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
                "plan_review DB lookup failed",
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
        is_ancestor, changed_files = await self._fetch_ancestor_and_changed_files(
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
        self,
        *,
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
            payload = await self._gitlab_client.compare_refs(
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
        self,
        event: GitLabMergeRequestEvent,
        plan: _ReviewPlan,
    ) -> dict[str, Any]:
        """按 plan.mode 取本次要送引擎的 GitLab changes payload。

        - full / reuse：直接走 MR changes 端点，语义等价旧路径。
        - incremental：仍走 MR changes 拿 base..head 完整 changes，再按
          ``plan.changed_files`` 过滤 changes 数组 —— 保证送给 LLM 的是
          "本次 push 改动的文件、但 diff 是完整 base..head 全量"。

        过滤后 changes 变空时保留其它字段（如 diff_refs）不动，让下游 pipeline
        继续走完（findings 为空，note 会写"无可审内容"）。
        """

        raw = await self._gitlab_client.get_merge_request_changes(
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

    async def _handle_reuse(
        self,
        *,
        event: GitLabMergeRequestEvent,
        plan: _ReviewPlan,
        policy_applied: str,
    ) -> OrchestratorResult | None:
        """head 未变的 CI 重跑：跳过 engine，把 parent review 结果重发 GitLab。

        - 不新建 review 行（避免同 head 产生 N 份重复历史）。
        - 重发 note：内容按 parent review 的 findings + 一个"复用上一次"横幅。
        - 重发 commit status：按 parent 的 has_blocker 决定 state。
        - parent 找不到 / DB 异常时返回 None，让主流程降级走 full 重审。
        """

        parent_id = plan.parent_review_id
        if parent_id is None or self._session_factory is None:
            return None
        try:
            async with self._session_factory() as session:
                parent = await session.get(ReviewRow, parent_id)
                if parent is None:
                    return None
                finding_repo = FindingRepository(session)
                parent_findings_rows = await finding_repo.list_by_review(parent_id)
        except SQLAlchemyError:
            logger.exception(
                "reuse lookup failed; will fall back to full review",
                extra={"parent_review_id": str(parent_id)},
            )
            return None

        engine_findings = [_finding_row_to_engine(row) for row in parent_findings_rows]
        has_blocker = bool(parent.has_blocker)
        blocker_count = parent.finding_count if has_blocker else 0
        note = await self._gitlab_client.create_merge_request_note(
            project_id=event.project_id,
            mr_iid=event.mr_iid,
            body=build_review_summary_note(
                review_id=parent.id,
                findings=engine_findings,
                has_blocker=has_blocker,
                blocker_count=blocker_count,
                policy_applied=policy_applied,
                detail_url=self._build_review_detail_url(parent.id),
                review_mode="reuse",
                mode_reason=plan.reason,
            ),
        )
        await self._gitlab_client.set_commit_status(
            project_id=event.project_id,
            commit_sha=event.source_commit_sha,
            state="failed" if has_blocker else "success",
            name="ai-code-reviewer",
            description=(
                f"AI Review reused: {parent.finding_count} finding(s), "
                f"{blocker_count} blocking"
                if has_blocker
                else f"AI Review reused: {parent.finding_count} finding(s)"
            ),
            target_url=self._build_review_detail_url(parent.id),
        )
        return OrchestratorResult(
            review_id=parent.id,
            project_uuid=event.project_uuid,
            status=parent.status,
            finding_count=parent.finding_count,
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            policy_applied=policy_applied,
            note_id=_extract_int(note, "id"),
        )

    async def _merge_findings_for_plan(
        self,
        event: GitLabMergeRequestEvent,
        plan: _ReviewPlan,
        new_findings: Sequence[Finding],
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
        if self._session_factory is None:
            return empty
        try:
            async with self._session_factory() as session:
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

    async def _resolve_stale_discussions_for_files(
        self,
        *,
        event: GitLabMergeRequestEvent,
        findings_to_resolve: Sequence[FindingRow],
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
                await self._gitlab_client.resolve_discussion(
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

    async def _handle_mr_closed(
        self, event: GitLabMergeRequestEvent,
    ) -> OrchestratorResult:
        """MR closed（非合并）：批量把 (project, mr) 的 open finding 标 ``mr_closed``。

        - 不跑 engine；不调 GitLab changes / note / commit status。
        - 插入一条 lifecycle 记账 Review 行（``status='done'``、
          ``review_mode='full'``、``finding_count=0``、``has_blocker=False``），保留时间线。
        - **不动 GitLab 侧 discussion**：MR 关闭时 GitLab 自己会灰化 discussion。
        - session_factory 未接入时全部 no-op，返回一个合成的 result 让上层保持
          "processed=True" 语义。

        Returns:
            :class:`OrchestratorResult`：``finding_count`` 报"受影响的 finding 数"，
            让 webhook 响应能被观测到；其余字段沿用 done 语义。
        """

        return await self._handle_lifecycle_event(
            event=event,
            terminal_status="mr_closed",
            log_label="mr_closed",
        )

    async def _handle_mr_merged(
        self, event: GitLabMergeRequestEvent,
    ) -> OrchestratorResult:
        """MR merged：批量把 (project, mr) 的 open finding 标 ``resolved``。

        与 close 分支的区别：finding 状态换成 ``resolved`` 并把
        ``resolved_in_review_id`` 指向本次 lifecycle 记账 review（``resolved`` 语义
        本来就要求带 resolver）。其余流程一致。
        """

        return await self._handle_lifecycle_event(
            event=event,
            terminal_status="resolved",
            log_label="mr_merged",
        )

    async def _handle_lifecycle_event(
        self,
        *,
        event: GitLabMergeRequestEvent,
        terminal_status: str,
        log_label: str,
    ) -> OrchestratorResult:
        """close / merge 共用的落库骨架：写 lifecycle Review + 批量翻状态。

        走一个事务：
          1. 插入 lifecycle Review 行（``status='done'``、``review_mode='full'``、
             ``finding_count=0``、``has_blocker=False``、``duration_ms=0``）；
          2. 调 :meth:`FindingRepository.mark_mr_closed` 或 :meth:`mark_resolved` 走
             单条 UPDATE + IN 子查询把所有 open finding 翻到 ``terminal_status``。

        session_factory / project 缺失均视为 no-op，返回一个合成的 result 而不是
        抛错，让 webhook 响应保持 processed=True。
        """

        review_id = uuid4()
        affected = 0
        if self._session_factory is not None:
            try:
                async with self._session_factory() as session:
                    project_repo = ProjectRepository(session)
                    project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
                    if project is None:
                        logger.warning(
                            "skip lifecycle event: project not registered",
                            extra={
                                "gitlab_project_id": event.project_id,
                                "mr_iid": event.mr_iid,
                                "action": event.action,
                            },
                        )
                    else:
                        review_row = ReviewRow(
                            id=review_id,
                            project_id=project.id,
                            mr_iid=str(event.mr_iid),
                            source_branch=event.source_branch,
                            target_branch=event.target_branch,
                            commit_sha=event.source_commit_sha,
                            status="done",
                            engine_used=None,
                            has_blocker=False,
                            finding_count=0,
                            duration_ms=0,
                            base_sha=event.target_commit_sha,
                            parent_review_id=None,
                            review_mode="full",
                            # PR #96：区分 lifecycle 记账与常规审查。前端根据此字段
                            # 渲染专属徽章（"MR 已关闭" / "MR 已合并"）。
                            lifecycle_event=(
                                "mr_closed"
                                if terminal_status == "mr_closed"
                                else "mr_merged"
                            ),
                        )
                        session.add(review_row)
                        await session.flush()

                        finding_repo = FindingRepository(session)
                        if terminal_status == "mr_closed":
                            affected = await finding_repo.mark_mr_closed(
                                project.id, str(event.mr_iid), review_id,
                            )
                        else:
                            # merged：先查出 (project, mr) 里所有 open finding 的 id，
                            # 再复用 mark_resolved（保证 resolved_in_review_id 与其它
                            # incremental 走的路径完全一致）。
                            open_rows = await finding_repo.list_open_by_mr(
                                project.id, str(event.mr_iid),
                            )
                            open_ids = [row.id for row in open_rows]
                            affected = len(open_ids)
                            await finding_repo.mark_resolved(open_ids, review_id)
                        await session.commit()
                        logger.info(
                            "mr lifecycle event applied",
                            extra={
                                "gitlab_project_id": event.project_id,
                                "mr_iid": event.mr_iid,
                                "action": event.action,
                                "lifecycle": log_label,
                                "affected_findings": affected,
                                "lifecycle_review_id": str(review_id),
                            },
                        )
            except SQLAlchemyError:
                logger.exception(
                    "failed to apply MR lifecycle event",
                    extra={
                        "gitlab_project_id": event.project_id,
                        "mr_iid": event.mr_iid,
                        "action": event.action,
                    },
                )
        return OrchestratorResult(
            review_id=review_id,
            project_uuid=event.project_uuid,
            status="done",
            # 用受影响 finding 数，webhook 响应侧可观测。
            finding_count=affected,
            has_blocker=False,
            blocker_count=0,
            policy_applied=None,
            note_id=None,
        )

    async def _reopen_mr_closed_findings(
        self, event: GitLabMergeRequestEvent,
    ) -> None:
        """MR reopen：把之前标记的 mr_closed 翻回 open，不插 Review 行。

        reopen 会紧接着继续跑常规增量审查，该审查会自己产出一条新 review；
        这里只做纯粹的翻转，避免记两条历史。DB 异常吞掉，退化为 no-op（下面
        的常规流程正常继续跑）。
        """

        if self._session_factory is None:
            return
        try:
            async with self._session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
                if project is None:
                    return
                finding_repo = FindingRepository(session)
                affected = await finding_repo.reopen_mr_closed(project.id, str(event.mr_iid))
                await session.commit()
                if affected:
                    logger.info(
                        "mr reopened; findings flipped back to open",
                        extra={
                            "gitlab_project_id": event.project_id,
                            "mr_iid": event.mr_iid,
                            "affected_findings": affected,
                        },
                    )
        except SQLAlchemyError:
            logger.exception(
                "failed to reopen mr_closed findings",
                extra={
                    "gitlab_project_id": event.project_id,
                    "mr_iid": event.mr_iid,
                },
            )

    async def _persist_review(
        self,
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

        if self._session_factory is None:
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
            async with self._session_factory() as session:
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

    def _build_review_detail_url(self, review_id: UUID) -> str | None:
        """Build an optional browser URL for the persisted review detail page."""

        if self._review_detail_base_url is None:
            return None
        return f"{self._review_detail_base_url}/reviews/{review_id}"

    async def _push_review_notification(
        self,
        *,
        event: GitLabMergeRequestEvent,
        review_id: UUID,
        finding_count: int,
        has_blocker: bool,
        blocker_count: int,
        status_value: str,
        findings: Sequence[Finding] | None = None,
        plan: _ReviewPlan | None = None,
    ) -> None:
        """推送 Review 完成通知（best-effort，失败不影响主流程）。

        成功 / 引擎异常两条路径共用：把评审摘要交给 :class:`NotificationService`，
        由其按项目配置的渠道分发。除旧有的计数字段外，还带上 MR 链接、作者信息
        （供 @ 创建人）、按严重级别分组的 ``findings_summary``（供正文列表）、
        MR 维度信息（标题 / 创建人 / 创建时间 / 链接）与变更文件数（供
        「MR信息 / 审查摘要」区块渲染）。
        未注入 ``notification_service`` 时直接跳过；任何异常（含推送失败）都被
        吞成 warning 日志，绝不阻断 Review 主流程。
        """

        if self._notification_service is None:
            return
        # incremental 模式下 plan.changed_files 才有值；full / None 时按 0 降级，
        # 通知侧对 0 会跳过「变更规模」行。
        changed_files_count = (
            len(plan.changed_files) if plan is not None and plan.changed_files else 0
        )
        try:
            await self._notification_service.send_review_completed(
                gitlab_project_id=event.project_id,
                review_data={
                    "review_id": str(review_id),
                    "mr_iid": event.mr_iid,
                    "mr_title": event.title,
                    "finding_count": finding_count,
                    "has_blocker": has_blocker,
                    "blocker_count": blocker_count,
                    "detail_url": self._build_review_detail_url(review_id),
                    "status": status_value,
                    "mr_author_username": event.author_username,
                    "mr_author_name": event.author_name,
                    "mr_web_url": event.web_url,
                    "findings_summary": _build_findings_summary(findings or []),
                    "mr_created_at": event.created_at,
                    "changed_files_count": changed_files_count,
                },
            )
        except Exception as exc:
            logger.warning("Failed to send review notification", exc_info=exc)

    async def _push_commit_review_notification(
        self,
        *,
        event: _CommitLikeEvent,
        review_id: UUID,
        finding_count: int,
        has_blocker: bool,
        blocker_count: int,
        status_value: str,
        findings: Sequence[Finding] | None = None,
    ) -> None:
        """推送 commit 审查完成通知（best-effort，失败不影响主流程）。

        参照 :meth:`_push_review_notification`，但 commit 审查没有 MR 上下文，
        ``mr_iid`` / ``mr_title`` 等 MR 语义字段用 commit 信息替代。
        未注入 ``notification_service`` 时直接跳过；任何异常（含推送失败）都被
        吞成 warning 日志，绝不阻断 commit 审查主流程。
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
                    "mr_created_at": "",  # commit 事件没有创建时间
                    "changed_files_count": 0,
                },
            )
        except Exception as exc:
            logger.warning("Failed to send commit review notification", exc_info=exc)


def _match_int(match: re.Match[str] | None, group: str, *, default: int) -> int:
    """Extract an int group from a regex match, returning ``default`` if absent."""

    if match is None:
        return default
    value = match.groupdict().get(group)
    if value is None:
        return default
    return int(value)


_SEVERITY_ORDER = ("BLOCKER", "WARNING", "INFO")


def _build_findings_summary(findings: Sequence[Finding]) -> list[dict[str, Any]]:
    """把 finding 列表按严重级别分组，构造成通知正文用的精简摘要。

    每组形如 ``{"severity": "BLOCKER", "items": [{"title", "file_path",
    "line_number"}, ...]}``，按 BLOCKER -> WARNING -> INFO 固定顺序输出；
    空级别不生成组。截断（WARNING/INFO 各最多 5 条等）由通知服务在渲染时
    决定，这里只负责全量分组。
    """

    summary: list[dict[str, Any]] = []
    for severity in _SEVERITY_ORDER:
        items = [
            {
                "title": finding.title,
                "file_path": finding.file_path,
                "line_number": finding.line_number,
            }
            for finding in findings
            if finding.severity == severity
        ]
        if items:
            summary.append({"severity": severity, "items": items})
    return summary


def _extract_int(payload: dict[str, Any], key: str) -> int | None:
    """Extract an optional integer from a response payload."""

    value = payload.get(key)
    return value if isinstance(value, int) else None


def _extract_diff_refs(
    changes_payload: dict[str, Any],
    event: GitLabMergeRequestEvent,
) -> dict[str, str]:
    """Return GitLab diff refs, falling back to webhook SHAs when absent."""

    raw_refs = changes_payload.get("diff_refs")
    refs = raw_refs if isinstance(raw_refs, dict) else {}
    base_sha = str(refs.get("base_sha") or event.target_commit_sha)
    start_sha = str(refs.get("start_sha") or event.target_commit_sha)
    head_sha = str(refs.get("head_sha") or event.source_commit_sha)
    return {"base_sha": base_sha, "start_sha": start_sha, "head_sha": head_sha}


def _is_line_number_valid_for_current_diff(
    changes_payload: dict[str, Any],
    file_path: str,
    line_number: int,
) -> bool:
    """
    检查 line_number 是否在当前 diff 对应文件的有效范围内。

    MR 关闭后再 push 新代码、重开 MR 时，旧 finding 的 line_number 可能已经
    和最新 diff 对不上。此时强行贴到旧行号会显示错误的代码区域，甚至
    完全不显示。

    返回 True：行号在当前 diff 的某个 hunk 范围内，可以安全贴行级评论。
    返回 False：行号已失效，降级成全局 MR 备注。
    """
    for change in changes_payload.get("changes", []):
        change_new_path = change.get("new_path")
        if change_new_path != file_path:
            continue

        # 文件已删除，肯定不能贴行级评论
        if change.get("deleted_file"):
            return False

        diff = change.get("diff", "")

        # 空 diff → 无代码改动，拒绝行级评论
        if not diff:
            return False

        # 有 hunk 才校验，不可解析的 diff → 保守允许创建（无法证实行号失效）
        has_hunks = _DIFF_HEADER_RE.search(diff) is not None
        if not has_hunks:
            return True

        # 解析 diff header，检查行号是否在某个 hunk 的 new_line 范围内
        for match in _DIFF_HEADER_RE.finditer(diff):
            new_start = int(match.group("new_start"))
            new_lines = int(match.group("new_lines") or 1)
            new_end = new_start + new_lines - 1

            if new_start <= line_number <= new_end:
                return True

        # 有 hunk 但行号不在范围内 → 真正失效了，降级成全局评论
        return False

    # 文件不在本次 diff 里 → 降级
    return False


def _resolve_finding_paths(changes_payload: dict[str, Any], file_path: str) -> tuple[str, str]:
    """Resolve old/new diff paths for a finding path from GitLab changes."""

    raw_changes = changes_payload.get("changes", [])
    if isinstance(raw_changes, list):
        for item in raw_changes:
            if not isinstance(item, dict):
                continue
            old_path = str(item.get("old_path") or "")
            new_path = str(item.get("new_path") or "")
            if file_path in {old_path, new_path}:
                return old_path or file_path, new_path or file_path
    return file_path, file_path


def _finding_row_to_engine(row: FindingRow) -> Finding:
    """把 DB 行投影回 engine.Finding，供合并展示与 reuse 复用。

    这里的 Finding 只用于 note / discussion 渲染，因此 ``source`` 用默认值
    （无法回溯规则来源），``existing_code`` / ``suggestion`` 保留原样。
    """

    severity = row.severity if row.severity in ("INFO", "WARNING", "BLOCKER") else "WARNING"
    return Finding(
        file_path=row.file_path,
        line_number=row.line_number,
        rule_id=row.rule_id,
        severity=severity,
        title=row.title,
        description=row.description,
        suggestion=row.suggestion,
        existing_code=row.existing_code,
        confidence=float(row.confidence or 0.0),
    )
