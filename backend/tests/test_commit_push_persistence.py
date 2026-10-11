"""push / commit 审查落库与查询链路测试（feat/commit-push-review-persistence）。

覆盖：
1. ``review_push`` 落库：done 路径写 ``reviews``（review_kind='push'）+ findings；
2. ``review_commit`` engine_error 路径落库：status='engine_error'，0 findings；
3. 空 diff 短路路径落库：done / 0 findings；
4. 项目未注册时跳过落库（best-effort，不抛异常）；
5. API 过滤：``review_kind`` 多值 / ``commit_sha`` 精确匹配；
6. Agent 轨迹端点：push 审查的 review 行 + trace 事件可查（此前 404 的断点）；
7. Dashboard 口径：``list_recent(review_kind='mr')`` 不含 push/commit 行。

需要真实数据库（CI 运行，与 test_commit_review.py 的 DB 测试同模式）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.admin import _sign_token
from engines import Finding, HealthStatus, ReviewContext, ReviewEngine
from engines.registry import EngineRegistry
from models.agent_trace_event import AgentTraceEvent
from models.finding import Finding as FindingRow
from models.project import Project
from models.review import Review as ReviewRow
from repositories.review import ReviewRepository
from services.review_orchestrator import GitLabCommitEvent, GitLabPushEvent, ReviewOrchestrator

# ---------------------------------------------------------------------------
# Fakes（与 test_commit_review.py 同构，独立维护避免跨文件耦合）
# ---------------------------------------------------------------------------


class _FakeGitLabClient:
    """commit / push 审查链路的 GitLab client 假实现。"""

    def __init__(self, *, diffs: list[dict[str, Any]]) -> None:
        self._diffs = diffs
        self.comments: list[dict[str, Any]] = []
        self.statuses: list[dict[str, Any]] = []

    async def get_commit(self, *, project_id: int, sha: str) -> dict[str, Any]:
        return {"id": sha, "title": "feat: demo", "parent_ids": ["parent000"]}

    async def get_commit_diff(self, *, project_id: int, sha: str) -> list[dict[str, Any]]:
        return self._diffs

    async def compare_refs(
        self,
        *,
        project_id: int,
        from_sha: str,
        to_sha: str,
    ) -> dict[str, Any]:
        return {"diffs": self._diffs, "commits": []}

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
        self.comments.append({"sha": sha, "note": note, "path": path, "line": line})
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
        self.statuses.append({"commit_sha": commit_sha, "state": state})
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


class _Policy:
    """block policy 测试替身（与 ORM 属性对齐）。"""

    def __init__(self, priority: int, branch_pattern: str, block_severity: str) -> None:
        self.priority = priority
        self.branch_pattern = branch_pattern
        self.block_severity = block_severity
        self.block_on_engine_error = False


_POLICIES = [
    _Policy(priority=1, branch_pattern="master", block_severity="BLOCKER"),
    _Policy(priority=99, branch_pattern="*", block_severity="NONE"),
]

_DIFF_APP_PY = {
    "new_path": "app.py",
    "old_path": "app.py",
    "diff": "@@ -1,3 +1,4 @@\n line1\n+new line\n line2\n",
    "new_file": False,
    "deleted_file": False,
}


def _finding(*, severity: str = "WARNING", line_number: int | None = 2) -> Finding:
    return Finding(
        file_path="app.py",
        line_number=line_number,
        rule_id="no-secret",
        severity=severity,  # type: ignore[arg-type]
        title="Secret leaked",
        description="Hard-coded secret detected.",
        suggestion="Move it to environment variables.",
        confidence=0.9,
    )


def _orchestrator(
    gitlab: _FakeGitLabClient,
    engine: _StaticEngine,
    session_factory: object | None = None,
) -> ReviewOrchestrator:
    registry = EngineRegistry()
    registry.register(engine)
    return ReviewOrchestrator(
        gitlab_client=gitlab,  # type: ignore[arg-type]
        engine_registry=registry,
        default_engine="static-engine",
        block_policies=_POLICIES,  # type: ignore[arg-type]
        session_factory=session_factory,  # type: ignore[arg-type]
    )


def _push_event(*, branch: str = "feature/x") -> GitLabPushEvent:
    return GitLabPushEvent(
        project_id=123,
        project_path="group/demo",
        branch=branch,
        before_sha="b" * 40,
        after_sha="a" * 40,
        commits=[
            {"id": "sha-1", "title": "first", "message": "first"},
            {"id": "sha-2", "title": "second", "message": "second"},
        ],
        author_username="alice",
        author_name="Alice Zhang",
    )


def _commit_event(*, branch: str = "feature/x") -> GitLabCommitEvent:
    return GitLabCommitEvent(
        project_id=123,
        project_path="group/demo",
        commit_sha="c0ffee0000",
        branch=branch,
        title="feat: demo",
        message="feat: demo",
    )


async def _create_project(session_factory: async_sessionmaker) -> Project:
    """在测试库里注册 GitLab project_id=123 的项目。"""

    project = Project(
        name="test-project",
        gitlab_project_id="123",
        gitlab_base_url="https://gitlab.example.com",
        gitlab_access_token="glpat-test",
        webhook_secret="test-webhook-secret",
        enabled=True,
        commit_review_enabled=True,
    )
    async with session_factory() as session:
        session.add(project)
        await session.commit()
        await session.refresh(project)
    return project


def _auth_headers() -> dict[str, str]:
    token = _sign_token("admin", datetime.now(UTC) + timedelta(hours=1))
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# review_push 落库
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_push_persists_review_and_findings(
    db_session_factory: async_sessionmaker,
) -> None:
    """push 合并审查完成后落库：review_kind='push'，findings 同步写入。"""

    await _create_project(db_session_factory)
    gitlab = _FakeGitLabClient(diffs=[_DIFF_APP_PY])
    engine = _StaticEngine([_finding(line_number=2), _finding(line_number=3)])
    orchestrator = _orchestrator(gitlab, engine, session_factory=db_session_factory)

    result = await orchestrator.review_push(_push_event())

    assert result.status == "done"
    assert result.review_id is not None
    async with db_session_factory() as session:
        review = await session.get(ReviewRow, result.review_id)
        assert review is not None
        assert review.review_kind == "push"
        assert review.mr_iid is None
        assert review.status == "done"
        assert review.finding_count == 2
        assert review.commit_sha == "a" * 40
        assert review.base_sha == "b" * 40
        assert review.source_branch == "feature/x"
        assert review.target_branch == "feature/x"
        findings = (
            (
                await session.execute(
                    select(FindingRow).where(FindingRow.review_id == result.review_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(findings) == 2


@pytest.mark.asyncio
async def test_review_commit_engine_error_persists_engine_error_row(
    db_session_factory: async_sessionmaker,
) -> None:
    """engine 异常路径也落库：status='engine_error'，0 findings，不装成功。"""

    await _create_project(db_session_factory)
    gitlab = _FakeGitLabClient(diffs=[_DIFF_APP_PY])
    engine = _StaticEngine(RuntimeError("llm timeout"))
    orchestrator = _orchestrator(gitlab, engine, session_factory=db_session_factory)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "engine_error"
    assert result.review_id is not None
    async with db_session_factory() as session:
        review = await session.get(ReviewRow, result.review_id)
        assert review is not None
        assert review.review_kind == "commit"
        assert review.status == "engine_error"
        assert review.finding_count == 0
        assert review.engine_used == "static-engine"


@pytest.mark.asyncio
async def test_review_push_empty_diff_persists_done_row(
    db_session_factory: async_sessionmaker,
) -> None:
    """空 diff 短路路径也落库：done / 0 findings（新页面能看到轨迹入口）。"""

    await _create_project(db_session_factory)
    gitlab = _FakeGitLabClient(diffs=[])
    engine = _StaticEngine([])
    orchestrator = _orchestrator(gitlab, engine, session_factory=db_session_factory)

    result = await orchestrator.review_push(_push_event())

    assert result.status == "done"
    assert result.review_id is not None
    async with db_session_factory() as session:
        review = await session.get(ReviewRow, result.review_id)
        assert review is not None
        assert review.review_kind == "push"
        assert review.status == "done"
        assert review.finding_count == 0


@pytest.mark.asyncio
async def test_persistence_skips_when_project_not_registered(
    db_session_factory: async_sessionmaker,
) -> None:
    """项目未注册：跳过落库（只记 warning），审查主流程照常完成。"""

    # 不注册 project（_create_project 不调用）。
    gitlab = _FakeGitLabClient(diffs=[_DIFF_APP_PY])
    engine = _StaticEngine([_finding(line_number=2)])
    orchestrator = _orchestrator(gitlab, engine, session_factory=db_session_factory)

    result = await orchestrator.review_commit(_commit_event())

    assert result.status == "done"
    async with db_session_factory() as session:
        review = await session.get(ReviewRow, result.review_id)
        assert review is None


# ---------------------------------------------------------------------------
# API 过滤（db_client）
# ---------------------------------------------------------------------------


async def _seed_reviews(db_session_factory: async_sessionmaker) -> None:
    """落 3 条不同 review_kind 的 review 行（mr / commit / push）。"""

    project = await _create_project(db_session_factory)
    async with db_session_factory() as session:
        for kind, sha in (("mr", "1111111111"), ("commit", "2222222222"), ("push", "3333333333")):
            session.add(
                ReviewRow(
                    id=uuid4(),
                    project_id=project.id,
                    mr_iid="42" if kind == "mr" else None,
                    source_branch="feature/x",
                    target_branch="master" if kind == "mr" else "feature/x",
                    commit_sha=sha,
                    status="done",
                    has_blocker=False,
                    finding_count=0,
                    review_mode="full",
                    review_kind=kind,
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_list_reviews_filters_by_review_kind_multi(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """review_kind 多值过滤：commit+push 命中 2 条，mr 被排除。"""

    await _seed_reviews(db_session_factory)

    response = await db_client.get(
        "/api/reviews/records?review_kind=commit&review_kind=push",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    kinds = {item["review_kind"] for item in data["items"]}
    assert kinds == {"commit", "push"}


@pytest.mark.asyncio
async def test_list_reviews_filters_by_commit_sha(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """commit_sha 精确过滤：命中 push 那一条。"""

    await _seed_reviews(db_session_factory)

    response = await db_client.get(
        "/api/reviews/records?commit_sha=3333333333",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["review_kind"] == "push"


@pytest.mark.asyncio
async def test_list_reviews_without_kind_filter_returns_all(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """不传 review_kind：不过滤来源（老行为），3 条全回。"""

    await _seed_reviews(db_session_factory)

    response = await db_client.get(
        "/api/reviews/records",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["total"] == 3


# ---------------------------------------------------------------------------
# Agent 轨迹端点（push 审查行 + trace 事件）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_trace_endpoint_returns_push_review_trace(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """push 审查落库后，轨迹端点能查到该 review 的 trace 事件（原断点修复）。"""

    project = await _create_project(db_session_factory)
    review_id = uuid4()
    async with db_session_factory() as session:
        session.add(
            ReviewRow(
                id=review_id,
                project_id=project.id,
                mr_iid=None,
                source_branch="feature/x",
                target_branch="feature/x",
                commit_sha="a" * 40,
                status="done",
                engine_used="llm-agent",
                review_kind="push",
            )
        )
        session.add(
            AgentTraceEvent(
                review_id=review_id,
                seq=1,
                event_type="run_started",
                payload={"model": "reviewer-1"},
            )
        )
        await session.commit()

    response = await db_client.get(
        f"/api/reviews/{review_id}/agent-trace",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    events = response.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "run_started"


# ---------------------------------------------------------------------------
# Dashboard 口径（list_recent 只回 MR）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_recent_filters_mr_only(
    db_session_factory: async_sessionmaker,
) -> None:
    """list_recent(review_kind='mr')：只回 MR 行，push/commit 行被排除。"""

    await _seed_reviews(db_session_factory)
    async with db_session_factory() as session:
        repo = ReviewRepository(session)
        rows = await repo.list_recent(limit=20, review_kind="mr")

    assert len(rows) == 1
    assert rows[0].review_kind == "mr"
