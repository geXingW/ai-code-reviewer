"""ReviewOrchestrator 持久化路径单测：
覆盖三条分支：
1. Project 在 DB 中注册 → 落 reviews + review_findings；
2. Project 未注册 → 跳过持久化，主流程仍返回正常 result；
3. 引擎失败 → 落 status='engine_error' 的 review 记录。

单测直接跑 ReviewOrchestrator，不经过 FastAPI，避免掺入 HTTP 层不确定性。
GitLab 客户端全部 mock，session_factory 用 test_engine 绑定当前 event loop 的
async_sessionmaker，避免和其它 test 里的模块级 AsyncSessionLocal 冲突。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Sequence
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.engines import Finding as EngineFinding
from app.engines import ReviewContext
from app.engines.registry import EngineRegistry
from app.models.finding import Finding as FindingRow
from app.models.project import Project
from app.models.review import Review as ReviewRow
from app.services.review_orchestrator import (
    GitLabMergeRequestEvent,
    ReviewOrchestrator,
)

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


class _StubEngine:
    """Return canned findings without calling any external service."""

    _NAME = "stub-engine"

    def __init__(self, findings: Sequence[EngineFinding] | Exception) -> None:
        self._findings = findings

    def name(self) -> str:
        return self._NAME

    async def review(self, context: ReviewContext) -> list[EngineFinding]:
        if isinstance(self._findings, Exception):
            raise self._findings
        return list(self._findings)


def _make_registry(engine_impl: _StubEngine) -> EngineRegistry:
    """Wrap a stub engine into a fresh EngineRegistry for isolation."""

    registry = EngineRegistry()
    registry.register(engine_impl)  # type: ignore[arg-type]
    return registry


def _make_gitlab_mock() -> AsyncMock:
    """Build a GitLabClient mock with sensible defaults for orchestrator flow."""

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


def _make_event(gitlab_project_id: int = 999) -> GitLabMergeRequestEvent:
    """Build a minimal MR event pinned to a specific GitLab project id."""

    return GitLabMergeRequestEvent(
        project_id=gitlab_project_id,
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


@pytest_asyncio.fixture
async def db_session_factory() -> AsyncGenerator[
    tuple[async_sessionmaker[AsyncSession], AsyncSession], None
]:
    """Create isolated schema + return (session_factory, session) tuple for one test."""

    test_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as connection:
        # pgcrypto 仅 PG 需要，MySQL 场景跳过。
        if TEST_DATABASE_URL.startswith("postgresql"):
            from sqlalchemy import text

            await connection.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        yield session_factory, session

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_persist_review_writes_review_and_findings_when_project_registered(
    db_session_factory: tuple[async_sessionmaker[AsyncSession], AsyncSession],
) -> None:
    """Project 存在时，评审完成后 reviews + review_findings 都应写入。"""

    session_factory, session = db_session_factory
    project = Project(
        name="registered-repo",
        gitlab_project_id="999",
        gitlab_access_token="tok",
        webhook_secret="sec",
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)

    finding = EngineFinding(
        file_path="app.py",
        line_number=2,
        rule_id="rule-1",
        severity="BLOCKER",
        title="hardcoded credential",
        description="detected hardcoded token",
        confidence=0.9,
    )
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(_StubEngine([finding])),
        default_engine="stub-engine",
        session_factory=session_factory,
    )
    result = await orch.review_merge_request(_make_event(gitlab_project_id=999))

    assert result.status == "done"
    assert result.finding_count == 1
    # 用一条独立 session 验证 DB 已落库
    async with session_factory() as verify:
        review_rows = (await verify.execute(select(ReviewRow))).scalars().all()
        finding_rows = (await verify.execute(select(FindingRow))).scalars().all()
    assert len(review_rows) == 1
    assert review_rows[0].status == "done"
    assert review_rows[0].finding_count == 1
    assert review_rows[0].project_id == project.id
    assert review_rows[0].commit_sha == "abc123"
    assert len(finding_rows) == 1
    assert finding_rows[0].rule_id == "rule-1"
    assert finding_rows[0].severity == "BLOCKER"


@pytest.mark.asyncio
async def test_persist_review_skips_when_project_not_registered(
    db_session_factory: tuple[async_sessionmaker[AsyncSession], AsyncSession],
) -> None:
    """Project 未注册时，主流程应正常返回 done，DB 里不写任何 review。"""

    session_factory, _ = db_session_factory

    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(_StubEngine([])),
        default_engine="stub-engine",
        session_factory=session_factory,
    )
    result = await orch.review_merge_request(_make_event(gitlab_project_id=8888))

    assert result.status == "done"
    async with session_factory() as verify:
        review_rows = (await verify.execute(select(ReviewRow))).scalars().all()
    assert review_rows == []


@pytest.mark.asyncio
async def test_engine_error_still_persists_review_row(
    db_session_factory: tuple[async_sessionmaker[AsyncSession], AsyncSession],
) -> None:
    """引擎抛异常时应落一条 status='engine_error' 记录，便于统计降级次数。"""

    session_factory, session = db_session_factory
    project = Project(
        name="err-repo",
        gitlab_project_id="777",
        gitlab_access_token="tok",
        webhook_secret="sec",
    )
    session.add(project)
    await session.commit()

    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(_StubEngine(RuntimeError("engine boom"))),
        default_engine="stub-engine",
        session_factory=session_factory,
    )
    result = await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert result.status == "engine_error"
    async with session_factory() as verify:
        review_rows = (await verify.execute(select(ReviewRow))).scalars().all()
    assert len(review_rows) == 1
    assert review_rows[0].status == "engine_error"
    assert review_rows[0].finding_count == 0


@pytest.mark.asyncio
async def test_persist_review_disabled_when_session_factory_is_none() -> None:
    """未注入 session_factory 时保持 MVP 行为：不查库、不落库。"""

    finding = EngineFinding(
        file_path="app.py",
        line_number=1,
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
    assert result.review_id is not None
    # review_id 是内存 uuid4，无外部依赖
    assert isinstance(result.review_id, type(uuid4()))
