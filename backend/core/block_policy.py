"""Branch block policy matching and blocker computation utilities."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Protocol, TypeAlias
from uuid import UUID

from models.project_block_policy import ProjectBlockPolicy

logger = logging.getLogger(__name__)

class Severity(StrEnum):
    """Supported finding severities emitted by review engines."""

    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKER = "BLOCKER"


class BlockSeverity(StrEnum):
    """Policy thresholds controlling whether findings block a merge."""

    NONE = "NONE"
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKER = "BLOCKER"
    ENGINE_ERROR_ONLY = "ENGINE_ERROR_ONLY"


class BlockPolicyLike(Protocol):
    """Minimal policy attributes required by the matching engine."""

    @property
    def priority(self) -> int:
        """Policy evaluation order; lower values have higher precedence."""
        ...

    @property
    def branch_pattern(self) -> str:
        """Glob pattern matched against the target branch."""
        ...

    @property
    def block_severity(self) -> str:
        """Persisted severity threshold value."""
        ...

    @property
    def block_on_engine_error(self) -> bool:
        """Whether engine execution errors should block the merge."""
        ...


class FindingLike(Protocol):
    """Minimal finding attributes required by blocker computation."""

    @property
    def severity(self) -> str:
        """Engine finding severity value."""
        ...


MatchedPolicy: TypeAlias = BlockPolicyLike

# 严重级别 → 数值秩。数值越大越严重，阻断判定用 "finding 秩 ≥ 阈值秩" 表达
# "阈值及以上都阻断"。刻意从 1 开始（而非 0）：让 "无严重级别 / 未知" 这类
# 非法值落不到合法秩上，便于 _finding_rank 直接报错而不是被误当成 INFO。
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 1,
    Severity.WARNING: 2,
    Severity.BLOCKER: 3,
}
# 新项目首次注册时用于 seed `project_block_policies` 的默认模板。
# 已存在的项目不会被自动升级：只影响未来新建项目。
# main / develop 与 master 同级视为主干分支。刻意不配置 `*` 兜底：
# 未匹配到策略的分支（如 feature/*）默认不触发 MR / commit 审核，
# 避免同一批提交在 feature push 与合并到主干后重复审核。
_DEFAULT_POLICY_TEMPLATES: tuple[tuple[int, str, BlockSeverity], ...] = (
    (1, "master", BlockSeverity.BLOCKER),
    (2, "main", BlockSeverity.BLOCKER),
    (3, "develop", BlockSeverity.BLOCKER),
    (4, "release/*", BlockSeverity.BLOCKER),
    (5, "hotfix/*", BlockSeverity.BLOCKER),
    (6, "test", BlockSeverity.BLOCKER),
)


def match_block_policy(
    policies: Iterable[BlockPolicyLike],
    target_branch: str,
) -> MatchedPolicy | None:
    """Return the first policy whose branch glob matches ``target_branch``.

    Policies are evaluated by ascending ``priority``. When no rule matches,
    returns ``None`` so callers can decide to skip the review (e.g. feature
    branches without a policy are not reviewed) instead of failing loudly.
    """
    logger.info(
        "Matching block policy for target branch",
        extra={
            "target_branch": target_branch,
            "policies": [
                {
                    "id": str(getattr(policy, "id", None)),
                    "branch_pattern": policy.branch_pattern,
                    "block_severity": policy.block_severity,
                    "priority": policy.priority,
                    "block_on_engine_error": policy.block_on_engine_error,
                }
                for policy in policies
            ],
        },
    )

    branch = target_branch.strip()
    if not branch:
        logger.info("target_branch must not be empty")
        raise ValueError("target_branch must not be empty")

    # 每次调用现排序，不依赖入参顺序：策略来自 DB，不同数据库/不同查询的返回
    # 顺序不保证一致（MySQL 无 ORDER BY 时尤其如此），而"谁先命中"直接决定
    # 阻断档位。策略数量是个位数，排序成本可忽略。
    ordered = sorted(policies, key=lambda policy: policy.priority)
    for policy in ordered:
        # 用 fnmatchcase（区分大小写）而不是 fnmatch：GitLab 分支名大小写敏感，
        # 若把 release/* 降低成 RELEASE/* 也命中，会误阻断不该阻断的分支。
        # 注意 fnmatch 的 * 会跨越 "/"，所以 release/* 同时覆盖 release/1.0/hotfix。
        if fnmatchcase(branch, policy.branch_pattern):
            logger.info(
                "Matched block policy",
                extra={
                    "target_branch": target_branch,
                    "policy": {
                        "id": str(getattr(policy, "id", None)),
                        "branch_pattern": policy.branch_pattern,
                        "block_severity": policy.block_severity,
                        "priority": policy.priority,
                        "block_on_engine_error": policy.block_on_engine_error,
                    },
                },
            )
            return policy

    logger.info(
        "No block policy matched target branch; review will be skipped",
        extra={"target_branch": target_branch},
    )
    return None


def compute_has_blocker(
    findings: Sequence[FindingLike],
    policy: BlockPolicyLike,
) -> tuple[bool, int]:
    """Compute whether findings should block under the selected policy.

    Returns:
        tuple[bool, int]: ``(has_blocker, blocker_count)`` where
        ``blocker_count`` is the number of findings at or above the configured
        threshold.
    """
    threshold = _parse_block_severity(policy.block_severity)
    logger.info(
        "Computing blocker for %d findings under policy",
        len(findings),
        extra={
            "findings": len(findings),
            "policy": {
                "id": str(getattr(policy, "id", None)),
                "branch_pattern": policy.branch_pattern,
                "block_severity": policy.block_severity,
                "priority": policy.priority,
                "block_on_engine_error": policy.block_on_engine_error,
            },
            "threshold": threshold,
        },
    )
    if threshold in {BlockSeverity.NONE, BlockSeverity.ENGINE_ERROR_ONLY}:
        # 这两档与 finding 严重级别无关：
        # - NONE：只评论不阻断；
        # - ENGINE_ERROR_ONLY：只在引擎失败时阻断（由
        #   compute_has_blocker_for_engine_error 单独判定），finding 再多也不阻断。
        logger.info(
            "Policy severity threshold allows all findings",
            extra={
                "threshold": threshold,
                "policy": {
                    "id": str(getattr(policy, "id", None)),
                    "branch_pattern": policy.branch_pattern,
                },
            },
        )
        return (False, 0)

    threshold_rank = _SEVERITY_RANK[Severity(threshold.value)]
    logger.info("Evaluating %d findings against severity threshold %r: %d",
                len(findings), threshold, threshold_rank)
    # "≥ 阈值" 语义：阈值 INFO 时任何 finding 都阻断；阈值 BLOCKER 时只有
    # BLOCKER 阻断。blocker_count 供 GitLab commit status 的描述文案使用
    # （"N finding(s), M blocking"），因此要求的是命中数而非布尔值。
    blocker_count = sum(
        1
        for finding in findings
        if _finding_rank(finding.severity) >= threshold_rank
    )
    return (blocker_count > 0, blocker_count)


def compute_has_blocker_for_engine_error(policy: BlockPolicyLike) -> tuple[bool, int]:
    """Compute blocking result when the review engine fails before findings exist."""

    threshold = _parse_block_severity(policy.block_severity)
    # 两个独立触发条件（任一成立即阻断）：
    # 1. 项目显式勾选了"引擎异常也阻断"（block_on_engine_error）；
    # 2. 策略档位本身就是 ENGINE_ERROR_ONLY —— 这类分支只关心引擎是否跑通。
    # 计数固定返回 1：引擎失败在语义上等价于"一条不可忽略的阻断项"，
    # 让上层 commit status 文案与 finding 路径保持同构。
    should_block = policy.block_on_engine_error or threshold == BlockSeverity.ENGINE_ERROR_ONLY
    return (should_block, 1 if should_block else 0)


def build_default_block_policies(project_id: UUID | str) -> list[ProjectBlockPolicy]:
    """Build default branch block policy ORM rows for a newly created project."""

    normalized_project_id = project_id if isinstance(project_id, UUID) else UUID(project_id)
    return [
        ProjectBlockPolicy(
            project_id=normalized_project_id,
            branch_pattern=branch_pattern,
            block_severity=block_severity.value,
            # 默认不因为"引擎跑挂了"阻断合并：新项目接入期模型/网络不稳，
            # 一上来就卡主线会让人关掉整个工具。需要严格门禁的项目再单独勾选。
            block_on_engine_error=False,
            # "所有 discussion 必须 resolved 才能合" 是 GitHub 式习惯，
            # GitLab 侧由项目自己的设置控制，这里默认关，避免双重门禁难排查。
            require_all_resolved=False,
            priority=priority,
        )
        for priority, branch_pattern, block_severity in _DEFAULT_POLICY_TEMPLATES
    ]


def _parse_block_severity(value: str) -> BlockSeverity:
    """Parse and validate persisted policy severity."""

    try:
        return BlockSeverity(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported block severity: {value}") from exc


def _finding_rank(value: str) -> int:
    """Return numeric severity rank for a finding severity string."""

    try:
        return _SEVERITY_RANK[Severity(value)]
    except ValueError as exc:
        # 未知 severity 直接抛错而不是当 INFO 处理：engine 万一吐出拼错的值
        # （如 "BLOCK"），静默降级会让本该阻断的 finding 悄悄放行。
        raise ValueError(f"Unsupported finding severity: {value}") from exc
