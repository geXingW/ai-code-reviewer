"""Branch block policy matching and blocker computation utilities."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Protocol, TypeAlias
from uuid import UUID

from app.models.project_block_policy import ProjectBlockPolicy


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
_DEFAULT_POLICY_TEMPLATES: tuple[tuple[int, str, BlockSeverity], ...] = (
    (1, "master", BlockSeverity.BLOCKER),
    (2, "release/*", BlockSeverity.BLOCKER),
    (3, "hotfix/*", BlockSeverity.BLOCKER),
    (99, "*", BlockSeverity.NONE),
)


def match_block_policy(
    policies: Iterable[BlockPolicyLike],
    target_branch: str,
) -> MatchedPolicy:
    """Return the first policy whose branch glob matches ``target_branch``.

    Policies are evaluated by ascending ``priority``. A catch-all ``*`` policy is
    expected for normal project configuration; when no rule matches, the helper
    raises ``ValueError`` so callers can surface a configuration problem instead
    of silently allowing a merge.
    """

    branch = target_branch.strip()
    if not branch:
        raise ValueError("target_branch must not be empty")

    ordered = sorted(policies, key=lambda policy: policy.priority)
    for policy in ordered:
        if fnmatchcase(branch, policy.branch_pattern):
            return policy
    raise ValueError(f"No block policy matched target branch: {target_branch}")


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
    if threshold in {BlockSeverity.NONE, BlockSeverity.ENGINE_ERROR_ONLY}:
        return (False, 0)

    threshold_rank = _SEVERITY_RANK[Severity(threshold.value)]
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
