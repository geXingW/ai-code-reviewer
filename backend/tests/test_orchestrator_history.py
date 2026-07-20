"""ReviewOrchestrator 负例反哺 (PR-B2) 单测。

覆盖 ``_resolve_history`` 的所有分支：短路 / scope 过滤 / limit / 排序 /
source finding 兜底 / explanation 兜底 / DB 异常 / 禁用规则 / 端到端硬过滤。

复用 test_orchestrator_rules_injection.py 的 fixture 结构。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# EncryptedString 需要 SECRET_KEY，与其它 orchestrator 单测一致，进程启动即固定。
os.environ.setdefault("SECRET_KEY", Fernet.generate_key().decode("utf-8"))

from app.core.config import get_settings  # noqa: E402
from app.core.db import Base  # noqa: E402
from app.engines import Finding as EngineFinding  # noqa: E402
from app.engines import ReviewContext  # noqa: E402
from app.engines.registry import EngineRegistry  # noqa: E402
from app.models.finding import Finding as FindingRow  # noqa: E402
from app.models.negative_example import NegativeExample  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.project_rule import ProjectRule  # noqa: E402
from app.models.review import Review as ReviewRow  # noqa: E402
from app.models.rule import Rule  # noqa: E402
from app.services.review_orchestrator import (  # noqa: E402
    GitLabMergeRequestEvent,
    ReviewOrchestrator,
)

get_settings.cache_clear()

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


class _CapturingEngine:
    """捕获 ReviewContext 供断言，不产出任何 finding。"""

    _NAME = "capture-engine"

    def __init__(self) -> None:
        self.captured: ReviewContext | None = None

    def name(self) -> str:
        return self._NAME

    async def review(self, context: ReviewContext) -> list[EngineFinding]:
        self.captured = context
        return []


def _make_registry(engine_impl: object) -> EngineRegistry:
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
            },
        ],
        "diff_refs": {"base_sha": "b", "start_sha": "s", "head_sha": "h"},
    }
    client.create_merge_request_note.return_value = {"id": 100}
    client.set_commit_status.return_value = {"status": "success"}
    client.create_merge_request_discussion.return_value = {"id": "d1"}
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


@pytest.fixture(autouse=True)
def _reset_settings_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例前后清 settings 缓存，避免 env 变量污染。"""

    get_settings.cache_clear()
    yield  # type: ignore[misc]
    get_settings.cache_clear()


async def _seed_project(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    name: str = "with-history",
    gitlab_project_id: str = "777",
) -> Project:
    """建一个最小 Project，返回持久化后的实例。"""

    async with session_factory() as session:
        project = Project(
            name=name,
            gitlab_project_id=gitlab_project_id,
            gitlab_access_token="tok",
            webhook_secret="sec",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


async def _seed_rule(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project: Project,
    rule_id: str,
    title: str = "Rule",
    enabled_link: bool = True,
    rule_enabled: bool = True,
) -> Rule:
    """建一条 Rule 并挂到 project 上。"""

    async with session_factory() as session:
        rule = Rule(
            rule_id=rule_id,
            title=title,
            prompt_snippet=f"snippet {rule_id}",
            severity_default="WARNING",
            enabled=rule_enabled,
        )
        session.add(rule)
        await session.flush()
        session.add(
            ProjectRule(project_id=project.id, rule_id=rule.id, enabled=enabled_link),
        )
        await session.commit()
        await session.refresh(rule)
        return rule


async def _add_negative_example(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    rule_id: str,
    project_id: object,  # UUID 或 None
    code_snippet: str = "foo()",
    explanation: str | None = "Not a real bug",
    source_finding_id: object = None,
    approved_at: datetime | None = None,
    approved_by: str | None = "admin",
) -> NegativeExample:
    """插入一条 NegativeExample。approved_at 为 None 表示"未批准"，仍能被拉。"""

    async with session_factory() as session:
        ne = NegativeExample(
            rule_id=rule_id,
            project_id=project_id,
            code_snippet=code_snippet,
            explanation=explanation,
            source_finding_id=source_finding_id,
            approved_by=approved_by,
            approved_at=approved_at,
        )
        session.add(ne)
        await session.commit()
        await session.refresh(ne)
        return ne


async def _add_source_finding(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project: Project,
    rule_id: str,
    file_path: str = "app.py",
    line_number: int | None = 10,
    title: str = "Bogus finding",
    description: str = "desc",
) -> FindingRow:
    """建一个真实 finding 作为负例 source_finding_id 的锚点。"""

    async with session_factory() as session:
        review = ReviewRow(
            project_id=project.id,
            mr_iid="1",
            source_branch="f/1",
            target_branch="master",
            commit_sha="c1",
            status="done",
            engine_used="llm-direct",
            has_blocker=False,
            finding_count=1,
        )
        session.add(review)
        await session.flush()
        finding = FindingRow(
            review_id=review.id,
            file_path=file_path,
            line_number=line_number,
            rule_id=rule_id,
            severity="WARNING",
            title=title,
            description=description,
            confidence=0.5,
        )
        session.add(finding)
        await session.commit()
        await session.refresh(finding)
        return finding


@pytest.mark.asyncio
async def test_history_short_circuit_when_max_items_zero(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_items=0 时直接短路，不查 DB，context.history 为空。"""

    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "0")
    get_settings.cache_clear()

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    # 塞 3 条负例，无论 scope 都本应被拉；但因 max_items=0 应保持空。
    for i in range(3):
        await _add_negative_example(
            session_factory,
            rule_id=f"r-{i}",
            project_id=project.id,
        )

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    assert capturing.captured.history == []


@pytest.mark.asyncio
async def test_history_scope_project_excludes_other_projects_and_global(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scope=project 只拉当前 project_id 的负例，排除其它项目和全局 (NULL)。"""

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "project")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "20")
    get_settings.cache_clear()

    session_factory = db_session_factory
    proj_a = await _seed_project(session_factory, name="A", gitlab_project_id="777")
    proj_b = await _seed_project(session_factory, name="B", gitlab_project_id="888")

    await _add_negative_example(
        session_factory, rule_id="r-a", project_id=proj_a.id, explanation="A"
    )
    await _add_negative_example(
        session_factory, rule_id="r-b", project_id=proj_b.id, explanation="B"
    )
    await _add_negative_example(
        session_factory, rule_id="r-g", project_id=None, explanation="G"
    )

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    history = capturing.captured.history
    assert [item.rule_id for item in history] == ["r-a"]


@pytest.mark.asyncio
async def test_history_scope_rule_matches_across_projects(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scope=rule 只按启用 rule_id 拉，包含全局与其它项目的负例，禁用规则跳过。"""

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "rule")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "20")
    get_settings.cache_clear()

    session_factory = db_session_factory
    proj_a = await _seed_project(session_factory, name="A", gitlab_project_id="777")
    proj_b = await _seed_project(session_factory, name="B", gitlab_project_id="888")

    # 项目 A 启用 rule X，rule Y 未挂 → context.rules 只含 X。
    await _seed_rule(session_factory, project=proj_a, rule_id="rule-x")

    # 负例：X 覆盖 A / B / 全局，Y 覆盖 A → 只有 X 的三条应被拉。
    await _add_negative_example(session_factory, rule_id="rule-x", project_id=proj_a.id)
    await _add_negative_example(session_factory, rule_id="rule-x", project_id=proj_b.id)
    await _add_negative_example(session_factory, rule_id="rule-x", project_id=None)
    await _add_negative_example(session_factory, rule_id="rule-y", project_id=proj_a.id)

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    history = capturing.captured.history
    assert len(history) == 3
    assert all(item.rule_id == "rule-x" for item in history)


@pytest.mark.asyncio
async def test_history_scope_both_union_and_dedup(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scope=both 取项目负例 ∪ 规则负例，SQL 层重复的按 id 去重。"""

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "both")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "20")
    get_settings.cache_clear()

    session_factory = db_session_factory
    proj_a = await _seed_project(session_factory, name="A", gitlab_project_id="777")
    proj_b = await _seed_project(session_factory, name="B", gitlab_project_id="888")
    await _seed_rule(session_factory, project=proj_a, rule_id="rule-x")

    # 三条负例：
    # 1) 项目 A 的 rule-x —— 同时被 project 和 rule 分支命中（去重后一次）
    # 2) 项目 A 的 rule-z（未启用）—— 只被 project 分支命中
    # 3) 项目 B 的 rule-x —— 只被 rule 分支命中
    ne_1 = await _add_negative_example(
        session_factory, rule_id="rule-x", project_id=proj_a.id, explanation="A/x"
    )
    ne_2 = await _add_negative_example(
        session_factory, rule_id="rule-z", project_id=proj_a.id, explanation="A/z"
    )
    ne_3 = await _add_negative_example(
        session_factory, rule_id="rule-x", project_id=proj_b.id, explanation="B/x"
    )

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    ids_in_history = {
        (item.rule_id, item.review_note) for item in capturing.captured.history
    }
    # 三条都应出现，且去重后不重复。
    assert len(capturing.captured.history) == 3
    assert ("rule-x", "A/x") in ids_in_history
    assert ("rule-z", "A/z") in ids_in_history
    assert ("rule-x", "B/x") in ids_in_history
    _ = (ne_1, ne_2, ne_3)


@pytest.mark.asyncio
async def test_history_limit_enforced(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_items 硬上限：塞 25 条，limit=20 时只拿到 20 条。"""

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "project")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "20")
    get_settings.cache_clear()

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    base = datetime.now(UTC) - timedelta(days=30)
    for i in range(25):
        # approved_at 递增，确保排序稳定。
        await _add_negative_example(
            session_factory,
            rule_id=f"r-{i:02d}",
            project_id=project.id,
            explanation=f"exp-{i}",
            approved_at=base + timedelta(minutes=i),
        )

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    assert len(capturing.captured.history) == 20


@pytest.mark.asyncio
async def test_history_order_recent_approved_first(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """approved_at DESC：最近批准的负例排前。"""

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "project")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "10")
    get_settings.cache_clear()

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    base = datetime.now(UTC) - timedelta(days=10)
    # 有意乱序落库，靠 approved_at 排序。
    await _add_negative_example(
        session_factory,
        rule_id="mid",
        project_id=project.id,
        approved_at=base + timedelta(days=5),
    )
    await _add_negative_example(
        session_factory,
        rule_id="oldest",
        project_id=project.id,
        approved_at=base,
    )
    await _add_negative_example(
        session_factory,
        rule_id="newest",
        project_id=project.id,
        approved_at=base + timedelta(days=9),
    )

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    assert [item.rule_id for item in capturing.captured.history] == [
        "newest",
        "mid",
        "oldest",
    ]


@pytest.mark.asyncio
async def test_history_source_finding_deleted_uses_unknown_fallback(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source finding 被 SET NULL 时 file_path 走 '(unknown)' 兜底文案。"""

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "project")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "5")
    get_settings.cache_clear()

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    # 负例的 source_finding_id 为 None（相当于 SET NULL 后的效果）。
    await _add_negative_example(
        session_factory,
        rule_id="orphan-rule",
        project_id=project.id,
        explanation="orphan",
        source_finding_id=None,
    )

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    assert len(capturing.captured.history) == 1
    item = capturing.captured.history[0]
    assert item.file_path == "(unknown)"
    assert item.line_number is None
    # 兜底 title 包含 rule_id 以便 prompt 能定位来源。
    assert "orphan-rule" in item.title
    assert item.description is None
    assert item.review_note == "orphan"


@pytest.mark.asyncio
async def test_history_source_finding_present_carries_context(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source_finding_id 命中时 file_path / title / description / line_number 全填回。"""

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "project")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "5")
    get_settings.cache_clear()

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    source = await _add_source_finding(
        session_factory,
        project=project,
        rule_id="known-rule",
        file_path="src/foo.py",
        line_number=42,
        title="Original bug title",
        description="Original description",
    )
    await _add_negative_example(
        session_factory,
        rule_id="known-rule",
        project_id=project.id,
        explanation="Confirmed noise",
        source_finding_id=source.id,
    )

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    assert len(capturing.captured.history) == 1
    item = capturing.captured.history[0]
    assert item.file_path == "src/foo.py"
    assert item.line_number == 42
    assert item.title == "Original bug title"
    assert item.description == "Original description"
    assert item.review_note == "Confirmed noise"


@pytest.mark.asyncio
async def test_history_explanation_empty_uses_review_note_fallback(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """explanation 为空时 review_note 走 'Confirmed false-positive; do not re-report.'"""

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "project")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "5")
    get_settings.cache_clear()

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    await _add_negative_example(
        session_factory,
        rule_id="quiet",
        project_id=project.id,
        explanation=None,
    )

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    assert len(capturing.captured.history) == 1
    assert (
        capturing.captured.history[0].review_note
        == "Confirmed false-positive; do not re-report."
    )


@pytest.mark.asyncio
async def test_history_db_error_is_swallowed(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """execute 抛异常时 context.history 保持空且不 crash，warning 打印。"""

    import logging
    from typing import Any

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "project")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "5")
    get_settings.cache_clear()

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    await _add_negative_example(
        session_factory, rule_id="r", project_id=project.id
    )

    # 只在拉负例的 SELECT 上抛异常，其它 execute 调用照旧。
    real_execute = AsyncSession.execute

    async def flaky_execute(  # noqa: ANN401
        self: AsyncSession,
        statement: Any,  # noqa: ANN401
        *args: Any,  # noqa: ANN401
        **kwargs: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        if "negative_examples" in str(statement).lower():
            msg = "simulated DB failure"
            raise RuntimeError(msg)
        return await real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", flaky_execute)

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    with caplog.at_level(logging.WARNING, logger="app.services.review_orchestrator"):
        await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    assert capturing.captured.history == []
    assert any(
        "failed to resolve history" in record.getMessage() for record in caplog.records
    )
    _ = project  # 保留引用避免 flake8 抱怨


@pytest.mark.asyncio
async def test_history_scope_rule_skips_disabled_rule(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scope=rule 时禁用的 ProjectRule 不参与 rule_id 命中，其负例被排除。"""

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "rule")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "10")
    get_settings.cache_clear()

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    # rule-on 启用；rule-off 关联但 ProjectRule.enabled=False → context.rules 不含它。
    await _seed_rule(session_factory, project=project, rule_id="rule-on")
    await _seed_rule(
        session_factory,
        project=project,
        rule_id="rule-off",
        enabled_link=False,
    )
    await _add_negative_example(
        session_factory, rule_id="rule-on", project_id=project.id, explanation="on"
    )
    await _add_negative_example(
        session_factory, rule_id="rule-off", project_id=project.id, explanation="off"
    )

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert capturing.captured is not None
    assert [item.rule_id for item in capturing.captured.history] == ["rule-on"]


@pytest.mark.asyncio
async def test_history_hard_filters_matching_finding_end_to_end(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端：注入的 history 被 engine 的 _matches_false_positive_history 硬过滤命中。"""

    monkeypatch.setenv("LLM_HISTORY_SCOPE", "project")
    monkeypatch.setenv("LLM_HISTORY_MAX_ITEMS", "5")
    get_settings.cache_clear()

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    source = await _add_source_finding(
        session_factory,
        project=project,
        rule_id="dup-rule",
        file_path="app.py",
        line_number=5,
        title="Same title",
        description="d",
    )
    await _add_negative_example(
        session_factory,
        rule_id="dup-rule",
        project_id=project.id,
        source_finding_id=source.id,
        explanation="already dismissed",
    )

    # 用一个"回放引擎"直接产 finding，模拟 LLM 输出的重复 finding。
    from app.engines.llm_engine.engine import (
        _matches_false_positive_history,
    )
    from app.engines.types import (
        FindingSource as _FindingSource,
    )
    from app.engines.types import (
        ReviewHistoryItem as _ReviewHistoryItem,  # noqa: F401
    )

    class _ReplayEngine:
        _NAME = "replay-engine"

        def __init__(self) -> None:
            self.captured_history: list[_ReviewHistoryItem] = []
            self.filtered_hit = False

        def name(self) -> str:
            return self._NAME

        async def review(self, context: ReviewContext) -> list[EngineFinding]:
            self.captured_history = list(context.history)
            candidate = EngineFinding(
                file_path="app.py",
                line_number=5,
                rule_id="dup-rule",
                severity="WARNING",
                title="Same title",
                description="d",
                source=_FindingSource.LLM_INFERRED,
            )
            if _matches_false_positive_history(candidate, context.history):
                self.filtered_hit = True
                return []
            return [candidate]

    replay = _ReplayEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(replay),
        default_engine="replay-engine",
        session_factory=session_factory,
    )
    result = await orch.review_merge_request(_make_event(gitlab_project_id=777))

    assert replay.filtered_hit is True
    assert len(replay.captured_history) == 1
    # 主流程返回值也应体现"没有 finding"。
    assert result.finding_count == 0
