"""commit 级自动审查（Push Hook -> 逐 commit 审查）单测。

覆盖：
1. ``_parse_push_event`` payload 解析（正常 / 删分支 / 非 heads ref / commits 空）；
2. ``ReviewOrchestrator.review_commit`` 编排（mock GitLabClient + fake engine）：
   正常路径 / merge commit / 根提交 / 行号失效 / engine 异常 / 空 diff；
3. 钉钉通知：正常完成 / 空审 / engine 异常三条路径都推送，推送失败被吞，
   跳过路径不推送；
4. 不落库：commit 审查不写 ``reviews`` / ``review_findings``，已有历史记录
   也不阻断重审（无幂等检查）；
5. schema：review_kind 写入值被 ReviewCreate / ReviewRead 接受。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.gitlab_webhook import _parse_push_event
from core.config import Settings
from engines import Finding, HealthStatus, ReviewContext, ReviewEngine
from engines.registry import EngineRegistry
from models.project import Project
from models.review import Review as ReviewRow
from schemas.review import ReviewCreate, ReviewRead
from services.review_orchestrator import (
    CommitReviewResult,
    GitLabCommitEvent,
    ReviewOrchestrator,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeCommitGitLabClient:
    """commit 审查链路用的 GitLab client 假实现，记录所有调用。"""

    commit_payload: dict[str, Any]
    diffs: list[dict[str, Any]]
    comments: list[dict[str, Any]] = field(default_factory=list)
    statuses: list[dict[str, Any]] = field(default_factory=list)
    api_calls: list[str] = field(default_factory=list)

    async def get_commit(self, *, project_id: int, sha: str) -> dict[str, Any]:
        self.api_calls.append("get_commit")
        return self.commit_payload

    async def get_commit_diff(self, *, project_id: int, sha: str) -> list[dict[str, Any]]:
        self.api_calls.append("get_commit_diff")
        return self.diffs

    async def create_commit_comment(
        self,
        *,
        project_id: int,
        sha: str,
        note: str,
        path: str | None = None,
        line: int | None = None,
        line_type: str | None = None,
    ) -> dict[str, Any]:
        self.api_calls.append("create_commit_comment")
        self.comments.append(
            {
                "project_id": project_id,
                "sha": sha,
                "note": note,
                "path": path,
                "line": line,
                "line_type": line_type,
            }
        )
        return {"id": len(self.comments)}

    async def set_commit_status(
        self,
        *,
        project_id: int,
        commit_sha: str,
        state: str,
        name: str,
        description: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        self.api_calls.append("set_commit_status")
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


class _StaticEngine(ReviewEngine):
    """返回预设 findings（或抛预设异常）的 engine 假实现。"""

    def __init__(self, findings: list[Finding] | Exception) -> None:
        self._findings = findings
        self.contexts: list[ReviewContext] = []

    def name(self) -> str:
        return "static-engine"

    def supports_feedback(self) -> bool:
        return True

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="ok")

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        self.contexts.append(ctx)
        if isinstance(self._findings, Exception):
            raise self._findings
        return list(self._findings)


@dataclass
class _FakeNotificationService:
    """记录 ``send_review_completed`` 调用的通知服务假实现。

    ``error`` 非空时抛出，用于验证推送失败不影响审查主流程。
    """

    calls: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None

    async def send_review_completed(
        self,
        *,
        gitlab_project_id: int,
        review_data: dict[str, Any],
    ) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(
            {
                "gitlab_project_id": gitlab_project_id,
                "review_data": review_data,
            }
        )


_DIFF_APP_PY = {
    "new_path": "app.py",
    "old_path": "app.py",
    "diff": "@@ -1,3 +1,4 @@\n line1\n+new line\n line2\n",
    "new_file": False,
    "deleted_file": False,
}


def _client(*, parent_ids: list[str] | None = None, diffs: list[dict[str, Any]] | None = None,
            ) -> _FakeCommitGitLabClient:
    return _FakeCommitGitLabClient(
        commit_payload={
            "id": "c0ffee0000",
            "title": "feat: demo",
            "parent_ids": parent_ids if parent_ids is not None else ["parent000"],
        },
        diffs=diffs if diffs is not None else [_DIFF_APP_PY],
    )


def _commit_event(
    *,
    branch: str = "feature/x",
    sha: str = "c0ffee0000",
    author_username: str | None = None,
    author_name: str | None = None,
) -> GitLabCommitEvent:
    return GitLabCommitEvent(
        project_id=123,
        project_path="group/demo",
        commit_sha=sha,
        branch=branch,
        title="feat: demo",
        message="feat: demo\n\nbody",
        author_username=author_username,
        author_name=author_name,
    )


def _finding(*, severity: str = "WARNING", line_number: int | None = 2,
             file_path: str = "app.py") -> Finding:
    return Finding(
        file_path=file_path,
        line_number=line_number,
        rule_id="no-secret",
        severity=severity,  # type: ignore[arg-type]
        title="Secret leaked",
        description="Hard-coded secret detected.",
        suggestion="Move it to environment variables.",
        confidence=0.9,
    )


def _orchestrator(
    gitlab: _FakeCommitGitLabClient,
    engine: _StaticEngine,
    session_factory: object | None = None,
    notification_service: _FakeNotificationService | None = None,
) -> ReviewOrchestrator:
    registry = EngineRegistry()
    registry.register(engine)
    return ReviewOrchestrator(
        gitlab_client=gitlab,  # type: ignore[arg-type]
        engine_registry=registry,
        default_engine="static-engine",
        session_factory=session_factory,  # type: ignore[arg-type]
        notification_service=notification_service,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# payload 解析
# ---------------------------------------------------------------------------


def _push_payload(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "object_kind": "push",
        "before": "old" * 10,
        "after": "new" * 10,
        "ref": "refs/heads/feature/x",
        "project": {"id": 123, "path_with_namespace": "group/demo"},
        "user": {"username": "alice", "name": "Alice Zhang"},
        "commits": [
            {"id": "sha-1", "title": "first", "message": "first\n\nbody"},
            {"id": "sha-2", "title": "second", "message": "second"},
        ],
    }
    payload.update(overrides or {})
    return payload


def test_parse_push_event_normal_payload() -> None:
    """正常 push payload：分支、commit 列表与 pusher 全部解析到位。"""

    info = _parse_push_event(_push_payload())

    assert info is not None
    assert info.project_id == 123
    assert info.project_path == "group/demo"
    assert info.branch == "feature/x"
    assert [c["id"] for c in info.commits] == ["sha-1", "sha-2"]
    assert info.commits[0]["title"] == "first"
    assert info.commits[0]["message"] == "first\n\nbody"
    assert info.pusher_username == "alice"
    assert info.pusher_name == "Alice Zhang"


def test_parse_push_event_ignores_branch_deletion() -> None:
    """after 为 40 个 0（删分支）-> None。"""

    assert _parse_push_event(_push_payload({"after": "0" * 40})) is None


def test_parse_push_event_ignores_non_heads_ref() -> None:
    """ref 非 refs/heads/*（如 tag push）-> None。"""

    assert _parse_push_event(_push_payload({"ref": "refs/tags/v1.0.0"})) is None


def test_parse_push_event_ignores_empty_commits() -> None:
    """commits 空列表 / 缺失 -> None。"""

    assert _parse_push_event(_push_payload({"commits": []})) is None
    assert _parse_push_event(_push_payload({"commits": None})) is None


# ---------------------------------------------------------------------------
# review_commit 编排（无 DB）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_commit_posts_anchored_comments_and_summary() -> None:
    """正常路径：N findings -> N 次锚定评论 + 1 次汇总评论 + 1 次 commit status。"""

    gitlab = _client()
    engine = _StaticEngine([_finding(line_number=2), _finding(line_number=3)])
    orchestrator = _orchestrator(gitlab, engine)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "done"
    assert result.finding_count == 2
    # 2 条锚定 + 1 条汇总 = 3 次评论。
    assert len(gitlab.comments) == 3
    anchored = gitlab.comments[:2]
    for comment in anchored:
        assert comment["path"] == "app.py"
        assert comment["line_type"] == "new"
        assert comment["line"] in (2, 3)
        assert "Secret leaked" in comment["note"]
    summary = gitlab.comments[-1]
    assert summary["path"] is None
    assert summary["line"] is None
    assert summary["line_type"] is None
    assert "AI Commit Review completed" in summary["note"]
    assert "2 finding(s)" in summary["note"]
    assert len(gitlab.statuses) == 1
    assert gitlab.statuses[0]["state"] == "success"
    assert gitlab.statuses[0]["commit_sha"] == "c0ffee0000"
    # engine 收到的 context：mr_iid 空串、diff 为 commit vs parent。
    assert len(engine.contexts) == 1
    ctx = engine.contexts[0]
    assert ctx.mr_iid == ""
    assert ctx.mr_title == "feat: demo"
    assert ctx.source_commit_sha == "c0ffee0000"
    assert ctx.target_commit_sha == "parent000"
    assert ctx.extra["review_kind"] == "commit"
    assert ctx.extra["review_base_sha"] == "parent000"


@pytest.mark.asyncio
async def test_review_commit_marks_failed_status_for_blocker_on_master() -> None:
    """BLOCKER finding 命中 master 默认阻断策略 -> commit status failed。"""

    gitlab = _client()
    engine = _StaticEngine([_finding(severity="BLOCKER")])
    orchestrator = _orchestrator(gitlab, engine)

    result = await orchestrator.review_commit(_commit_event(branch="master"))

    assert result.status == "done"
    assert result.has_blocker is True
    assert gitlab.statuses[0]["state"] == "failed"


@pytest.mark.asyncio
async def test_review_commit_skips_merge_commit() -> None:
    """merge commit（2 个 parent）-> skipped_merge_commit，无评论无 status 无通知。"""

    gitlab = _client(parent_ids=["p1", "p2"])
    engine = _StaticEngine([])
    notifier = _FakeNotificationService()
    orchestrator = _orchestrator(gitlab, engine, notification_service=notifier)

    result = await orchestrator.review_commit(_commit_event())

    assert _assert_skipped(result, "skipped_merge_commit", gitlab, engine)
    assert notifier.calls == []


@pytest.mark.asyncio
async def test_review_commit_skips_root_commit() -> None:
    """根提交（无 parent）-> skipped_root_commit。"""

    gitlab = _client(parent_ids=[])
    engine = _StaticEngine([])
    orchestrator = _orchestrator(gitlab, engine)

    result = await orchestrator.review_commit(_commit_event())

    assert _assert_skipped(result, "skipped_root_commit", gitlab, engine)


def _assert_skipped(
    result: CommitReviewResult,
    expected_status: str,
    gitlab: _FakeCommitGitLabClient,
    engine: _StaticEngine,
) -> bool:
    """跳过类结果的公共断言：无 engine 调用、无评论、无 status。"""

    assert result.status == expected_status
    assert result.review_id is None
    assert engine.contexts == []
    assert gitlab.comments == []
    assert gitlab.statuses == []
    # get_commit 仍被调用（判断 parents 必需），但不应走到 diff。
    assert "get_commit_diff" not in gitlab.api_calls
    return True


@pytest.mark.asyncio
async def test_review_commit_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """全局开关关闭 -> skipped_disabled，不调任何 GitLab API。"""

    gitlab = _client()
    engine = _StaticEngine([])
    orchestrator = _orchestrator(gitlab, engine)

    settings = Settings(commit_review_enabled=False)
    monkeypatch.setattr(
        "services.review_orchestrator.get_settings", lambda: settings,
    )

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "skipped_disabled"
    assert gitlab.api_calls == []


@pytest.mark.asyncio
async def test_review_commit_invalid_line_number_degrades_to_no_comment() -> None:
    """行号失效的 finding -> 不发锚定评论，汇总评论照发。"""

    gitlab = _client()
    # hunk 有效 new 行范围 1..4，line 99 失效。
    engine = _StaticEngine([_finding(line_number=99), _finding(line_number=2)])
    orchestrator = _orchestrator(gitlab, engine)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "done"
    # 只有 1 条有效锚定 + 1 条汇总。
    anchored = [c for c in gitlab.comments if c["path"] is not None]
    assert len(anchored) == 1
    assert anchored[0]["line"] == 2
    assert any(c["path"] is None for c in gitlab.comments)


@pytest.mark.asyncio
async def test_review_commit_engine_error_fails_loudly() -> None:
    """engine 异常 -> engine_error + commit status failed（绝不装 success/done）。"""

    gitlab = _client()
    engine = _StaticEngine(RuntimeError("llm timeout"))
    orchestrator = _orchestrator(gitlab, engine)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "engine_error"
    assert result.status != "done"
    assert gitlab.statuses[0]["state"] == "failed"
    assert gitlab.statuses[0]["state"] != "success"
    summary = gitlab.comments[-1]
    assert "FAILED" in summary["note"]
    assert "AI Review engine failed" in summary["note"]


@pytest.mark.asyncio
async def test_review_commit_empty_diff_completes_with_zero_findings() -> None:
    """diff 过滤后为空 -> done + 0 findings + "无可审查变更"评论。"""

    gitlab = _client(diffs=[])
    engine = _StaticEngine([])
    orchestrator = _orchestrator(gitlab, engine)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "done"
    assert result.finding_count == 0
    # 不跑 engine。
    assert engine.contexts == []
    assert len(gitlab.comments) == 1
    assert "无可审查变更" in gitlab.comments[0]["note"]
    assert gitlab.statuses[0]["state"] == "success"


# ---------------------------------------------------------------------------
# 通知推送（fake NotificationService）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_commit_pushes_notification_on_completion() -> None:
    """正常完成 -> 推送一条通知，commit 语义字段（短 SHA / 标题 / 作者）到位。"""

    gitlab = _client()
    engine = _StaticEngine([_finding(line_number=2)])
    notifier = _FakeNotificationService()
    orchestrator = _orchestrator(gitlab, engine, notification_service=notifier)

    result = await orchestrator.review_commit(
        _commit_event(author_username="alice", author_name="Alice Zhang"),
    )

    assert result.status == "done"
    assert len(notifier.calls) == 1
    call = notifier.calls[0]
    assert call["gitlab_project_id"] == 123
    data = call["review_data"]
    assert data["status"] == "done"
    assert data["finding_count"] == 1
    assert data["has_blocker"] is False
    assert data["blocker_count"] == 0
    assert data["mr_iid"] == "c0ffee00"  # commit 短 SHA 替代 MR iid
    assert data["mr_title"] == "feat: demo"
    assert data["mr_author_username"] == "alice"
    assert data["mr_author_name"] == "Alice Zhang"
    assert data["mr_web_url"] is None
    # 1 条 WARNING finding 进了摘要。
    assert len(data["findings_summary"]) == 1
    assert data["findings_summary"][0]["severity"] == "WARNING"


@pytest.mark.asyncio
async def test_review_commit_notification_carries_blocker_stats() -> None:
    """BLOCKER 命中阻断策略 -> 通知带 has_blocker / blocker_count。"""

    gitlab = _client()
    engine = _StaticEngine([_finding(severity="BLOCKER")])
    notifier = _FakeNotificationService()
    orchestrator = _orchestrator(gitlab, engine, notification_service=notifier)

    await orchestrator.review_commit(_commit_event(branch="master"))

    assert len(notifier.calls) == 1
    data = notifier.calls[0]["review_data"]
    assert data["has_blocker"] is True
    assert data["blocker_count"] == 1


@pytest.mark.asyncio
async def test_review_commit_pushes_notification_on_empty_diff() -> None:
    """空 diff -> done + 0 findings 的通知照推。"""

    gitlab = _client(diffs=[])
    engine = _StaticEngine([])
    notifier = _FakeNotificationService()
    orchestrator = _orchestrator(gitlab, engine, notification_service=notifier)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "done"
    assert len(notifier.calls) == 1
    data = notifier.calls[0]["review_data"]
    assert data["status"] == "done"
    assert data["finding_count"] == 0
    assert data["findings_summary"] == []


@pytest.mark.asyncio
async def test_review_commit_pushes_notification_on_engine_error() -> None:
    """engine 异常 -> engine_error 通知照推（绝不静默失败）。"""

    gitlab = _client()
    engine = _StaticEngine(RuntimeError("llm timeout"))
    notifier = _FakeNotificationService()
    orchestrator = _orchestrator(gitlab, engine, notification_service=notifier)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "engine_error"
    assert len(notifier.calls) == 1
    data = notifier.calls[0]["review_data"]
    assert data["status"] == "engine_error"
    assert data["finding_count"] == 0
    assert data["findings_summary"] == []


@pytest.mark.asyncio
async def test_review_commit_notification_failure_does_not_break_flow() -> None:
    """通知推送抛异常 -> 吞掉，审查结果与 GitLab 写回不受影响。"""

    gitlab = _client()
    engine = _StaticEngine([_finding(line_number=2)])
    notifier = _FakeNotificationService(error=RuntimeError("dingtalk down"))
    orchestrator = _orchestrator(gitlab, engine, notification_service=notifier)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "done"
    assert result.finding_count == 1
    assert len(gitlab.comments) == 2  # 1 锚定 + 1 汇总
    assert gitlab.statuses[0]["state"] == "success"


# ---------------------------------------------------------------------------
# 不落库（真实测试 DB）
# ---------------------------------------------------------------------------


async def _create_project(session_factory: async_sessionmaker) -> Project:
    """在测试库里注册 GitLab project_id=123 的项目。"""

    project = Project(
        name="test-project",
        gitlab_project_id="123",
        gitlab_base_url="https://gitlab.example.com",
        gitlab_access_token="glpat-test",
        webhook_secret="test-webhook-secret",
        enabled=True,
    )
    async with session_factory() as session:
        session.add(project)
        await session.commit()
        await session.refresh(project)
    return project


@pytest.mark.asyncio
async def test_review_commit_reruns_despite_existing_done_record(
    db_session_factory: async_sessionmaker,
) -> None:
    """commit 审查无幂等检查：已有 done 的历史记录也照样重新审查。"""

    project = await _create_project(db_session_factory)
    async with db_session_factory() as session:
        session.add(
            ReviewRow(
                id=uuid4(),
                project_id=project.id,
                mr_iid=None,
                source_branch="feature/x",
                target_branch="feature/x",
                commit_sha="c0ffee0000",
                status="done",
                has_blocker=False,
                finding_count=0,
                review_mode="full",
                review_kind="commit",
            )
        )
        await session.commit()

    gitlab = _client()
    engine = _StaticEngine([])
    orchestrator = _orchestrator(gitlab, engine, session_factory=db_session_factory)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "done"
    assert "get_commit" in gitlab.api_calls


@pytest.mark.asyncio
async def test_review_commit_does_not_persist_review_or_finding_rows(
    db_session_factory: async_sessionmaker,
) -> None:
    """审查完成后不落库：reviews / review_findings 都查不到本次审查。"""

    await _create_project(db_session_factory)
    gitlab = _client()
    engine = _StaticEngine([_finding(line_number=2)])
    orchestrator = _orchestrator(gitlab, engine, session_factory=db_session_factory)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "done"
    assert result.review_id is not None
    from sqlalchemy import select

    from models.finding import Finding as FindingRow

    async with db_session_factory() as session:
        review = await session.get(ReviewRow, result.review_id)
        assert review is None
        findings = (
            (
                await session.execute(
                    select(FindingRow).where(FindingRow.review_id == result.review_id)
                )
            )
            .scalars()
            .all()
        )
        assert findings == []


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("review_kind", ["mr", "commit"])
def test_review_kind_literal_accepts_written_values(review_kind: str) -> None:
    """ReviewCreate 接受 'mr' / 'commit'（schema 允许的两个取值）。"""

    payload = ReviewCreate(
        project_id="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
        mr_iid=None,
        source_branch="feature/x",
        target_branch="feature/x",
        commit_sha="c0ffee0000",
        review_kind=review_kind,  # type: ignore[arg-type]
    )
    assert payload.review_kind == review_kind


@pytest.mark.parametrize("review_kind", ["mr", "commit"])
def test_review_read_accepts_review_kind(review_kind: str) -> None:
    """ReviewRead 接受历史 commit 审查行（mr_iid=None + review_kind）。"""

    from datetime import UTC, datetime

    read = ReviewRead(
        id="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
        project_id="00000000-0000-0000-0000-000000000002",  # type: ignore[arg-type]
        mr_iid=None,
        source_branch="feature/x",
        target_branch="feature/x",
        commit_sha="c0ffee0000",
        status="done",
        engine_used="llm-direct",
        provider_used=None,
        policy_applied=None,
        has_blocker=False,
        finding_count=0,
        duration_ms=10,
        raw_llm_output=None,
        review_kind=review_kind,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert read.review_kind == review_kind
    assert read.mr_iid is None
