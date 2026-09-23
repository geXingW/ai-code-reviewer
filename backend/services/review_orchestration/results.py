"""Result dataclasses and internal planning structures for the orchestration package."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from engines import Finding
from models.finding import Finding as FindingRow


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
