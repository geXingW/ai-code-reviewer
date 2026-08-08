"""Tests that ReviewOrchestrator triggers review-completed notifications.

Covers both the success path (``status="done"``) and the engine-error path
(``status="engine_error"``), plus the backward-compatible case where no
notification service is injected. All paths run with ``session_factory=None``
so no database is required -- persistence is skipped and only the notification
call is observed via a mock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from engines import Finding as EngineFinding
from engines.registry import EngineRegistry
from services.review_orchestrator import GitLabMergeRequestEvent, ReviewOrchestrator


class _StubEngine:
    """Return canned findings (or raise) without calling any external service."""

    _NAME = "stub-engine"

    def __init__(self, findings_or_exc: list[EngineFinding] | Exception) -> None:
        self._findings = findings_or_exc

    def name(self) -> str:
        return self._NAME

    async def review(self, context: object) -> list[EngineFinding]:
        if isinstance(self._findings, Exception):
            raise self._findings
        return list(self._findings)


def _make_registry(engine: _StubEngine) -> EngineRegistry:
    registry = EngineRegistry()
    registry.register(engine)  # type: ignore[arg-type]
    return registry


def _make_gitlab_mock() -> MagicMock:
    """Build a GitLabClient mock with sensible defaults for the orchestrator flow."""

    client = AsyncMock()
    client.get_merge_request_changes.return_value = {
        "changes": [
            {
                "diff": "@@ -1,3 +1,4 @@\n line1\n+new line\n line2\n",
                "new_path": "app.py",
                "old_path": "app.py",
                "new_file": False,
                "deleted_file": False,
            }
        ],
        "diff_refs": {"base_sha": "b", "start_sha": "s", "head_sha": "h"},
    }
    client.create_merge_request_note.return_value = {"id": 100}
    client.set_commit_status.return_value = {"status": "success"}
    client.create_merge_request_discussion.return_value = {"id": "d1"}
    return client


def _make_event() -> GitLabMergeRequestEvent:
    return GitLabMergeRequestEvent(
        project_id=999,
        project_path="group/repo",
        mr_iid=42,
        source_branch="feature/x",
        target_branch="master",
        source_commit_sha="abc123",
        target_commit_sha="def456",
        action="open",
        title="test MR",
        web_url="http://gitlab.example.com/mr/42",
    )


def _make_notification_service() -> MagicMock:
    """Build a NotificationService mock exposing an AsyncMock send_review_completed."""

    notif = MagicMock()
    notif.send_review_completed = AsyncMock()
    return notif


@pytest.mark.asyncio
async def test_orchestrator_notifies_on_success() -> None:
    """Review 成功完成时应以 ``status="done"`` 调用通知服务，传递评审摘要。"""

    finding = EngineFinding(
        file_path="app.py",
        rule_id="rule-1",
        severity="BLOCKER",
        title="hardcoded credential",
        confidence=0.9,
    )
    notif = _make_notification_service()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(_StubEngine([finding])),
        default_engine="stub-engine",
        session_factory=None,
        notification_service=notif,
    )

    result = await orch.review_merge_request(_make_event())

    assert result.status == "done"
    notif.send_review_completed.assert_awaited_once()
    kwargs = notif.send_review_completed.await_args.kwargs
    assert kwargs["gitlab_project_id"] == 999
    review_data = kwargs["review_data"]
    assert review_data["status"] == "done"
    assert review_data["finding_count"] == 1
    assert review_data["mr_iid"] == 42
    assert review_data["mr_title"] == "test MR"


@pytest.mark.asyncio
async def test_orchestrator_notifies_on_engine_error() -> None:
    """引擎异常时应以 ``status="engine_error"`` 调用通知服务，finding_count 为 0。"""

    notif = _make_notification_service()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(_StubEngine(RuntimeError("engine boom"))),
        default_engine="stub-engine",
        session_factory=None,
        notification_service=notif,
    )

    result = await orch.review_merge_request(_make_event())

    assert result.status == "engine_error"
    notif.send_review_completed.assert_awaited_once()
    review_data = notif.send_review_completed.await_args.kwargs["review_data"]
    assert review_data["status"] == "engine_error"
    assert review_data["finding_count"] == 0


@pytest.mark.asyncio
async def test_orchestrator_without_notification_service_does_not_error() -> None:
    """未注入通知服务时主流程仍正常返回，保持旧调用方兼容。"""

    finding = EngineFinding(
        file_path="app.py",
        rule_id="rule-x",
        severity="WARNING",
        title="stub",
    )
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(_StubEngine([finding])),
        default_engine="stub-engine",
        session_factory=None,
    )

    result = await orch.review_merge_request(_make_event())

    assert result.status == "done"
    assert result.finding_count == 1


@pytest.mark.asyncio
async def test_orchestrator_notification_failure_does_not_break_main_flow() -> None:
    """通知服务自身抛异常时被 orchestrator 兜住，主流程仍返回 done。"""

    finding = EngineFinding(
        file_path="app.py",
        rule_id="rule-1",
        severity="WARNING",
        title="stub",
    )
    notif = _make_notification_service()
    notif.send_review_completed = AsyncMock(side_effect=RuntimeError("notify down"))
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(_StubEngine([finding])),
        default_engine="stub-engine",
        session_factory=None,
        notification_service=notif,
    )

    result = await orch.review_merge_request(_make_event())

    assert result.status == "done"
