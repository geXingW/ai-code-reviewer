"""Tests for the webhook-triggered review orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.engines import Finding, HealthStatus, ReviewContext, ReviewEngine
from app.engines.registry import EngineRegistry
from app.services.review_orchestrator import (
    GitLabMergeRequestEvent,
    ReviewOrchestrator,
)


class _RecordingEngine(ReviewEngine):
    """Engine test double that records the context it receives."""

    def __init__(self) -> None:
        self.contexts: list[ReviewContext] = []

    def name(self) -> str:
        return "recording"

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        self.contexts.append(ctx)
        return []

    def supports_feedback(self) -> bool:
        return True

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="ok")


@dataclass(frozen=True)
class _Policy:
    """Policy test double matching the blocker engine contract."""

    priority: int
    branch_pattern: str
    block_severity: str
    block_on_engine_error: bool = False


@dataclass
class _FakeGitLabClient:
    """Fake GitLab client used by orchestrator tests."""

    changes: dict
    notes: list[str] = field(default_factory=list)
    statuses: list[dict] = field(default_factory=list)

    async def get_merge_request_changes(self, project_id: int, mr_iid: int) -> dict:
        assert project_id == 123
        assert mr_iid == 7
        return self.changes

    async def create_merge_request_note(self, project_id: int, mr_iid: int, body: str) -> dict:
        assert project_id == 123
        assert mr_iid == 7
        self.notes.append(body)
        return {"id": 1, "body": body}

    async def set_commit_status(
        self,
        project_id: int,
        commit_sha: str,
        state: str,
        name: str,
        description: str,
        target_url: str | None = None,
    ) -> dict:
        self.statuses.append(
            {
                "project_id": project_id,
                "commit_sha": commit_sha,
                "state": state,
                "name": name,
                "description": description,
                "target_url": target_url,
            }
        )
        return {"status": state}


def _event(*, target_branch: str = "master") -> GitLabMergeRequestEvent:
    return GitLabMergeRequestEvent(
        project_id=123,
        project_path="group/demo",
        mr_iid=7,
        source_branch="feature/demo",
        target_branch=target_branch,
        source_commit_sha="abc123",
        target_commit_sha="base456",
        action="open",
        title="Demo MR",
        web_url="https://gitlab.example.com/group/demo/-/merge_requests/7",
    )


class _FindingEngine(_RecordingEngine):
    """Engine test double that returns configured findings."""

    def __init__(self, findings: list[Finding]) -> None:
        super().__init__()
        self._findings = findings

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        self.contexts.append(ctx)
        return self._findings


def _finding(*, severity: str) -> Finding:
    return Finding(
        file_path="app.py",
        line_number=10,
        rule_id="no-secret",
        severity=severity,
        title="Secret leaked",
        description="Hard-coded secret detected.",
        suggestion="Move it to environment variables.",
        confidence=0.91,
    )


def _registry(engine: ReviewEngine) -> EngineRegistry:
    registry = EngineRegistry()
    registry.register(engine)
    return registry


@pytest.mark.asyncio
async def test_orchestrator_runs_engine_and_posts_no_findings_note() -> None:
    """Happy path: diff -> ReviewContext -> engine -> GitLab note/status."""

    engine = _RecordingEngine()
    gitlab = _FakeGitLabClient(
        changes={
            "changes": [
                {
                    "new_path": "app.py",
                    "old_path": "app.py",
                    "diff": "@@ -1 +1 @@\n-print('old')\n+print('new')\n",
                    "new_file": False,
                    "deleted_file": False,
                }
            ]
        }
    )
    orchestrator = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="recording",
    )

    result = await orchestrator.review_merge_request(_event())

    assert result.status == "done"
    assert result.finding_count == 0
    assert len(engine.contexts) == 1
    ctx = engine.contexts[0]
    assert ctx.project_id == result.project_uuid
    assert ctx.mr_iid == "7"
    assert ctx.diff_hunks[0].file_path == "app.py"
    assert "AI Review completed" in gitlab.notes[0]
    assert gitlab.statuses[0]["state"] == "success"


@pytest.mark.asyncio
async def test_orchestrator_posts_blocking_status_when_default_policy_blocks_master() -> None:
    """Default master policy maps BLOCKER findings to failed commit status."""

    gitlab = _FakeGitLabClient(changes={"changes": []})
    orchestrator = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(_FindingEngine([_finding(severity="BLOCKER")])),
        default_engine="recording",
    )

    result = await orchestrator.review_merge_request(_event(target_branch="master"))

    assert result.status == "done"
    assert result.has_blocker is True
    assert result.finding_count == 1
    assert "Secret leaked" in gitlab.notes[0]
    assert "app.py:10" in gitlab.notes[0]
    assert gitlab.statuses[0]["state"] == "failed"
    assert "1 blocking" in gitlab.statuses[0]["description"]


@pytest.mark.asyncio
async def test_orchestrator_allows_blocker_on_feature_branch_by_default_policy() -> None:
    """Default catch-all policy is NONE, so feature branches should not be blocked."""

    gitlab = _FakeGitLabClient(changes={"changes": []})
    orchestrator = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(_FindingEngine([_finding(severity="BLOCKER")])),
        default_engine="recording",
    )

    result = await orchestrator.review_merge_request(_event(target_branch="feature/demo"))

    assert result.has_blocker is False
    assert result.finding_count == 1
    assert gitlab.statuses[0]["state"] == "success"


@pytest.mark.asyncio
async def test_orchestrator_uses_injected_policy_threshold() -> None:
    """Injected policy thresholds should allow WARNING findings to block selected branches."""

    gitlab = _FakeGitLabClient(changes={"changes": []})
    orchestrator = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(_FindingEngine([_finding(severity="WARNING")])),
        default_engine="recording",
        block_policies=[
            _Policy(priority=1, branch_pattern="release/*", block_severity="WARNING"),
            _Policy(priority=99, branch_pattern="*", block_severity="NONE"),
        ],
    )

    result = await orchestrator.review_merge_request(_event(target_branch="release/v1.2"))

    assert result.has_blocker is True
    assert result.finding_count == 1
    assert gitlab.statuses[0]["state"] == "failed"
    assert "1 blocking" in gitlab.statuses[0]["description"]
