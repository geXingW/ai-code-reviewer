"""ReviewOrchestrator rules 注入路径单测（Issue #78）。

覆盖：
1. Project 关联启用规则 → ReviewContext.rules 包含 RuleSpec（title/desc/severity 正确）；
2. ProjectRule.enabled=False → 该规则被过滤；
3. Rule.enabled=False → 该规则被过滤；
4. Project 未在管理后台注册 → rules 为空；
5. session_factory=None → rules 为空；
6. severity_override 生效（覆盖 rule.severity_default）。

复用与 test_orchestrator_provider_injection.py 相同的 _CapturingEngine 结构。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.engines import Finding as EngineFinding
from app.engines import ReviewContext
from app.engines.registry import EngineRegistry
from app.models.project import Project
from app.models.project_rule import ProjectRule
from app.models.rule import Rule
from app.services.review_orchestrator import (
    GitLabMergeRequestEvent,
    ReviewOrchestrator,
)

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


class _CapturingEngine:
    """捕获 ReviewContext 用于断言。"""

    _NAME = "capture-engine"

    def __init__(self) -> None:
        self.captured: ReviewContext | None = None

    def name(self) -> str:
        return self._NAME

    async def review(self, context: ReviewContext) -> list[EngineFinding]:
        self.captured = context
        return []


def _make_registry(engine_impl: _CapturingEngine) -> EngineRegistry:
    registry = EngineRegistry()
    registry.register(engine_impl)  # type: ignore[arg-type]
    return registry


def _make_gitlab_mock() -> AsyncMock:
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
    return client


def _make_event(gitlab_project_id: int = 777) -> GitLabMergeRequestEvent:
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
    async_sessionmaker[AsyncSession], None
]:
    """每个用例一份干净 schema。"""

    test_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as connection:
        if TEST_DATABASE_URL.startswith("postgresql"):
            from sqlalchemy import text

            await connection.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    yield session_factory

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_rules_injected_when_project_has_enabled_rules(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Project 关联启用规则时，ReviewContext.rules 应含对应 RuleSpec。"""

    session_factory = db_session_factory
    async with session_factory() as session:
        project = Project(
            name="with-rules",
            gitlab_project_id="777",
            gitlab_access_token="tok",
            webhook_secret="sec",
        )
        rule_sql = Rule(
            rule_id="general.sql-injection",
            title="SQL 注入风险",
            prompt_snippet="警惕拼接 SQL，优先使用参数化。",
            severity_default="BLOCKER",
        )
        rule_npe = Rule(
            rule_id="java.null-safety",
            title="潜在 NPE",
            prompt_snippet="访问对象前判空。",
            severity_default="WARNING",
        )
        session.add_all([project, rule_sql, rule_npe])
        await session.flush()
        session.add_all(
            [
                ProjectRule(project_id=project.id, rule_id=rule_sql.id, enabled=True),
                ProjectRule(project_id=project.id, rule_id=rule_npe.id, enabled=True),
            ]
        )
        await session.commit()

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    rules = capturing.captured.rules
    assert len(rules) == 2
    rule_ids = {r.rule_id for r in rules}
    assert rule_ids == {"general.sql-injection", "java.null-safety"}
    sql_spec = next(r for r in rules if r.rule_id == "general.sql-injection")
    assert sql_spec.title == "SQL 注入风险"
    assert sql_spec.description == "警惕拼接 SQL，优先使用参数化。"
    assert sql_spec.severity == "BLOCKER"


@pytest.mark.asyncio
async def test_rules_filter_out_disabled_project_rule_link(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ProjectRule.enabled=False 时该关联规则应被过滤。"""

    session_factory = db_session_factory
    async with session_factory() as session:
        project = Project(
            name="with-disabled-link",
            gitlab_project_id="778",
            gitlab_access_token="tok",
            webhook_secret="sec",
        )
        rule_a = Rule(
            rule_id="r-a",
            title="A",
            prompt_snippet="a",
            severity_default="WARNING",
        )
        rule_b = Rule(
            rule_id="r-b",
            title="B",
            prompt_snippet="b",
            severity_default="WARNING",
        )
        session.add_all([project, rule_a, rule_b])
        await session.flush()
        session.add_all(
            [
                ProjectRule(project_id=project.id, rule_id=rule_a.id, enabled=True),
                ProjectRule(project_id=project.id, rule_id=rule_b.id, enabled=False),
            ]
        )
        await session.commit()

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=778))

    assert capturing.captured is not None
    assert [r.rule_id for r in capturing.captured.rules] == ["r-a"]


@pytest.mark.asyncio
async def test_rules_filter_out_globally_disabled_rule(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Rule.enabled=False 时该规则应被全局过滤，即便 ProjectRule.enabled=True。"""

    session_factory = db_session_factory
    async with session_factory() as session:
        project = Project(
            name="with-globally-disabled",
            gitlab_project_id="779",
            gitlab_access_token="tok",
            webhook_secret="sec",
        )
        rule_on = Rule(
            rule_id="r-on",
            title="On",
            prompt_snippet="on",
            severity_default="INFO",
            enabled=True,
        )
        rule_off = Rule(
            rule_id="r-off",
            title="Off",
            prompt_snippet="off",
            severity_default="INFO",
            enabled=False,
        )
        session.add_all([project, rule_on, rule_off])
        await session.flush()
        session.add_all(
            [
                ProjectRule(project_id=project.id, rule_id=rule_on.id, enabled=True),
                ProjectRule(project_id=project.id, rule_id=rule_off.id, enabled=True),
            ]
        )
        await session.commit()

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=779))

    assert capturing.captured is not None
    assert [r.rule_id for r in capturing.captured.rules] == ["r-on"]


@pytest.mark.asyncio
async def test_rules_empty_when_project_not_registered(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Project 未注册时，rules 应为空。"""

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=db_session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=9999))

    assert capturing.captured is not None
    assert capturing.captured.rules == []


@pytest.mark.asyncio
async def test_rules_empty_when_session_factory_absent() -> None:
    """session_factory=None 时，rules 应为空。"""

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=None,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    assert capturing.captured.rules == []


@pytest.mark.asyncio
async def test_rules_severity_override_takes_precedence(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ProjectRule.severity_override 应覆盖 Rule.severity_default。"""

    session_factory = db_session_factory
    async with session_factory() as session:
        project = Project(
            name="with-override",
            gitlab_project_id="780",
            gitlab_access_token="tok",
            webhook_secret="sec",
        )
        rule = Rule(
            rule_id="r-sev",
            title="Sev",
            prompt_snippet="sev",
            severity_default="INFO",
        )
        session.add_all([project, rule])
        await session.flush()
        session.add(
            ProjectRule(
                project_id=project.id,
                rule_id=rule.id,
                enabled=True,
                severity_override="BLOCKER",
            )
        )
        await session.commit()

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=780))

    assert capturing.captured is not None
    assert len(capturing.captured.rules) == 1
    assert capturing.captured.rules[0].severity == "BLOCKER"
