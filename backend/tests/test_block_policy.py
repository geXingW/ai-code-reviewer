"""Tests for branch block policy matching and blocker computation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.core.block_policy import (
    BlockSeverity,
    Severity,
    build_default_block_policies,
    compute_has_blocker,
    compute_has_blocker_for_engine_error,
    match_block_policy,
)


@dataclass(frozen=True)
class BP:
    """Small policy double matching the ORM attributes used by the engine."""

    priority: int
    branch_pattern: str
    block_severity: str
    block_on_engine_error: bool = False


@dataclass(frozen=True)
class F:
    """Small finding double matching the severity attribute used by the engine."""

    severity: str


def test_match_block_policy_uses_priority_order_and_fnmatch_patterns() -> None:
    """The lowest priority matching glob wins, regardless of input order."""

    policies = [
        BP(priority=99, branch_pattern="*", block_severity="NONE"),
        BP(priority=2, branch_pattern="release/*", block_severity="BLOCKER"),
        BP(priority=1, branch_pattern="master", block_severity="BLOCKER"),
    ]

    assert match_block_policy(policies, "master").block_severity == "BLOCKER"
    assert match_block_policy(policies, "release/v1.2").block_severity == "BLOCKER"
    assert match_block_policy(policies, "feature/x").block_severity == "NONE"


def test_match_block_policy_raises_when_no_policy_matches() -> None:
    """A missing fallback is a configuration error and should fail loudly."""

    with pytest.raises(ValueError, match="No block policy matched"):
        match_block_policy([BP(1, "master", "BLOCKER")], "feature/x")


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [
        (BlockSeverity.NONE, (False, 0)),
        (BlockSeverity.INFO, (True, 3)),
        (BlockSeverity.WARNING, (True, 2)),
        (BlockSeverity.BLOCKER, (True, 1)),
        (BlockSeverity.ENGINE_ERROR_ONLY, (False, 0)),
    ],
)
def test_compute_has_blocker_counts_findings_at_or_above_policy_threshold(
    threshold: BlockSeverity,
    expected: tuple[bool, int],
) -> None:
    """Severity thresholds should count all findings at or above the threshold."""

    findings = [F("INFO"), F("WARNING"), F("BLOCKER")]
    policy = BP(priority=1, branch_pattern="master", block_severity=threshold.value)

    assert compute_has_blocker(findings, policy) == expected


def test_compute_has_blocker_rejects_unknown_severity_values() -> None:
    """Unknown persisted values should fail closed instead of being ignored."""

    with pytest.raises(ValueError, match="Unsupported finding severity"):
        compute_has_blocker([F("CRITICAL")], BP(1, "master", "BLOCKER"))


def test_engine_error_blocking_is_controlled_by_policy_flag_and_threshold() -> None:
    """Engine errors block only when the explicit flag or dedicated threshold says so."""

    assert compute_has_blocker_for_engine_error(
        BP(1, "master", "BLOCKER", block_on_engine_error=False),
    ) == (False, 0)
    assert compute_has_blocker_for_engine_error(
        BP(1, "master", "BLOCKER", block_on_engine_error=True),
    ) == (True, 1)
    assert compute_has_blocker_for_engine_error(
        BP(1, "master", "ENGINE_ERROR_ONLY", block_on_engine_error=False),
    ) == (True, 1)


def test_default_block_policy_seed_templates_match_issue_contract() -> None:
    """New project defaults should protect master/release/hotfix and allow others."""

    project_id = uuid4()
    defaults = build_default_block_policies(project_id=project_id)

    assert [(p.priority, p.branch_pattern, p.block_severity) for p in defaults] == [
        (1, "master", BlockSeverity.BLOCKER.value),
        (2, "release/*", BlockSeverity.BLOCKER.value),
        (3, "hotfix/*", BlockSeverity.BLOCKER.value),
        (99, "*", BlockSeverity.NONE.value),
    ]
    assert all(p.project_id == project_id for p in defaults)
    assert all(p.block_on_engine_error is False for p in defaults)


def test_default_block_policy_seed_accepts_string_project_id() -> None:
    """Seed helper should accept UUID strings from API/service layers."""

    project_id = uuid4()

    defaults = build_default_block_policies(project_id=str(project_id))

    assert all(isinstance(p.project_id, UUID) for p in defaults)
    assert {p.project_id for p in defaults} == {project_id}


def test_public_enums_define_supported_policy_and_finding_values() -> None:
    """Enums centralize supported severities instead of duplicating literals."""

    assert [item.value for item in Severity] == ["INFO", "WARNING", "BLOCKER"]
    assert [item.value for item in BlockSeverity] == [
        "NONE",
        "INFO",
        "WARNING",
        "BLOCKER",
        "ENGINE_ERROR_ONLY",
    ]
