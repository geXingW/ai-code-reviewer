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

    ordered = sorted(policies, key=lambda policy: policy.priority)
    for policy in ordered:
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
    blocker_count = sum(
        1
        for finding in findings
        if _finding_rank(finding.severity) >= threshold_rank
    )
    return (blocker_count > 0, blocker_count)


def compute_has_blocker_for_engine_error(policy: BlockPolicyLike) -> tuple[bool, int]:
    """Compute blocking result when the review engine fails before findings exist."""

    threshold = _parse_block_severity(policy.block_severity)
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
            block_on_engine_error=False,
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
        raise ValueError(f"Unsupported finding severity: {value}") from exc
