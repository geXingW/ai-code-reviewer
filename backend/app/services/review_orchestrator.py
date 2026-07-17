"""Review orchestration from GitLab merge request events to engine execution.

This module intentionally keeps persistence optional for the MVP. It constructs the
runtime :class:`app.engines.types.ReviewContext`, runs the selected engine, writes
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
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.block_policy import (
    BlockPolicyLike,
    build_default_block_policies,
    compute_has_blocker,
    compute_has_blocker_for_engine_error,
    match_block_policy,
)
from app.core.diff_filter import DiffFilterConfig, filter_gitlab_changes
from app.core.summary_builder import (
    build_finding_discussion_body,
    build_review_summary_note,
)
from app.engines import DiffHunk, Finding, ProviderConfig, ReviewContext, RuleSpec
from app.engines.registry import EngineRegistry, get_engine_registry
from app.integrations.gitlab.client import GitLabClient, GitLabClientError
from app.models.finding import Finding as FindingRow
from app.models.review import Review as ReviewRow
from app.repositories.project import ProjectRepository
from app.repositories.provider import ProviderRepository
from app.repositories.review import FindingRepository, ReviewRepository

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
        action: GitLab MR action, e.g. ``open`` or ``update``.
        title: Merge request title.
        web_url: Browser URL of the merge request.
        description: MR 描述正文；来自 ``object_attributes.description``，可能为空。
        last_commit_message: MR head 分支最近一次 commit 的 message；来自
            ``object_attributes.last_commit.message``，可能为空。
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

    async def review_merge_request(self, event: GitLabMergeRequestEvent) -> OrchestratorResult:
        """Run the configured review engine for one GitLab MR event.

        Args:
            event: Normalized merge request event.

        Returns:
            OrchestratorResult: Aggregate execution summary.
        """

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
            rules=await self._resolve_rules(event),
            mr_title=event.title,
            mr_description=event.description,
            last_commit_message=event.last_commit_message,
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
        event: GitLabMergeRequestEvent,
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
        event: GitLabMergeRequestEvent,
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


def _match_int(match: re.Match[str] | None, group: str, *, default: int) -> int:
    """Extract an int group from a regex match, returning ``default`` if absent."""

    if match is None:
        return default
    value = match.groupdict().get(group)
    if value is None:
        return default
    return int(value)


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
