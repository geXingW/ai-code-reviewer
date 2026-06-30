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
    discussions: list[dict] = field(default_factory=list)
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

    async def create_merge_request_discussion(
        self,
        *,
        project_id: int,
        mr_iid: int,
        body: str,
        base_sha: str,
        start_sha: str,
        head_sha: str,
        old_path: str,
        new_path: str,
        line_number: int,
    ) -> dict:
        assert project_id == 123
        assert mr_iid == 7
        payload = {
            "body": body,
            "base_sha": base_sha,
            "start_sha": start_sha,
            "head_sha": head_sha,
            "old_path": old_path,
            "new_path": new_path,
            "line_number": line_number,
        }
        self.discussions.append(payload)
        return {"id": f"discussion-{len(self.discussions)}", **payload}

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


class _FailingEngine(_RecordingEngine):
    """Engine test double that raises during review."""

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        self.contexts.append(ctx)
        raise RuntimeError("provider timeout token=secret-token internal_url=http://llm.local")


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
            "diff_refs": {
                "base_sha": "base-diff-sha",
                "start_sha": "start-diff-sha",
                "head_sha": "head-diff-sha",
            },
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

    gitlab = _FakeGitLabClient(
        changes={
            "diff_refs": {
                "base_sha": "base-diff-sha",
                "start_sha": "start-diff-sha",
                "head_sha": "head-diff-sha",
            },
            "changes": [
                {
                    "new_path": "app.py",
                    "old_path": "old-app.py",
                    "diff": "@@ -10 +10 @@\n-old\n+new\n",
                    "deleted_file": False,
                    "binary": False,
                }
            ],
        }
    )
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
    assert gitlab.discussions == [
        {
            "body": (
                "**[BLOCKER] Secret leaked**\n\n"
                "Hard-coded secret detected.\n\n"
                "Suggestion: Move it to environment variables."
            ),
            "base_sha": "base-diff-sha",
            "start_sha": "start-diff-sha",
            "head_sha": "head-diff-sha",
            "old_path": "old-app.py",
            "new_path": "app.py",
            "line_number": 10,
        }
    ]
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


@pytest.mark.asyncio
async def test_orchestrator_filters_unreviewable_changes_before_engine() -> None:
    """Ignored, binary, deleted, and oversized diffs should not reach engines."""

    engine = _RecordingEngine()
    gitlab = _FakeGitLabClient(
        changes={
            "changes": [
                {
                    "new_path": "src/app.py",
                    "old_path": "src/app.py",
                    "diff": "@@ -1 +1 @@\n-old\n+new\n",
                    "deleted_file": False,
                    "binary": False,
                },
                {
                    "new_path": "dist/bundle.js",
                    "old_path": "dist/bundle.js",
                    "diff": "+generated\n",
                    "deleted_file": False,
                    "binary": False,
                },
                {
                    "new_path": "asset.png",
                    "old_path": "asset.png",
                    "diff": "",
                    "deleted_file": False,
                    "binary": True,
                },
                {
                    "new_path": "too-large.txt",
                    "old_path": "too-large.txt",
                    "diff": "+" + ("x" * 30),
                    "deleted_file": False,
                    "binary": False,
                },
            ]
        }
    )
    orchestrator = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="recording",
        ignore_paths=("dist/**",),
        max_diff_bytes=30,
    )

    await orchestrator.review_merge_request(_event())

    assert [hunk.file_path for hunk in engine.contexts[0].diff_hunks] == ["src/app.py"]


@pytest.mark.asyncio
async def test_orchestrator_filters_full_diff_size_before_engine() -> None:
    """Full diff size should be bounded, not only added/removed lines."""

    engine = _RecordingEngine()
    gitlab = _FakeGitLabClient(
        changes={
            "changes": [
                {
                    "new_path": "small-change-large-context.py",
                    "old_path": "small-change-large-context.py",
                    "diff": "@@ -1,4 +1,4 @@\n" + (" context\n" * 20) + "-old\n+new\n",
                    "deleted_file": False,
                    "binary": False,
                }
            ]
        }
    )
    orchestrator = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="recording",
        max_diff_bytes=80,
    )

    await orchestrator.review_merge_request(_event())

    assert engine.contexts[0].diff_hunks == []


@pytest.mark.asyncio
async def test_orchestrator_converts_engine_error_to_failed_status_when_policy_blocks() -> None:
    """Engine errors should become a deterministic blocking result when policy says so."""

    gitlab = _FakeGitLabClient(changes={"changes": []})
    orchestrator = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(_FailingEngine()),
        default_engine="recording",
        block_policies=[
            _Policy(
                priority=1,
                branch_pattern="master",
                block_severity="ENGINE_ERROR_ONLY",
                block_on_engine_error=True,
            ),
            _Policy(priority=99, branch_pattern="*", block_severity="NONE"),
        ],
    )

    result = await orchestrator.review_merge_request(_event(target_branch="master"))

    assert result.status == "engine_error"
    assert result.has_blocker is True
    assert result.finding_count == 0
    assert result.blocker_count == 1
    assert result.policy_applied == "master -> ENGINE_ERROR_ONLY"
    assert "AI Review engine failed before producing findings." in gitlab.notes[0]
    assert "provider timeout" not in gitlab.notes[0]
    assert "secret-token" not in gitlab.notes[0]
    assert "llm.local" not in gitlab.notes[0]
    assert gitlab.statuses[0]["state"] == "failed"


@pytest.mark.asyncio
async def test_orchestrator_converts_engine_error_to_success_when_policy_allows() -> None:
    """Non-blocking branches should surface engine errors without blocking CI."""

    gitlab = _FakeGitLabClient(changes={"changes": []})
    orchestrator = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(_FailingEngine()),
        default_engine="recording",
        block_policies=[
            _Policy(priority=99, branch_pattern="*", block_severity="NONE"),
        ],
    )

    result = await orchestrator.review_merge_request(_event(target_branch="feature/demo"))

    assert result.status == "engine_error"
    assert result.has_blocker is False
    assert result.blocker_count == 0
    assert gitlab.statuses[0]["state"] == "success"
