"""Repository 层下沉方法（PR4）单测。

覆盖两个新方法的核心分支：

- :meth:`NegativeExampleRepository.list_approved_for_history`：scope 三分支
  WHERE 组装、LEFT OUTER JOIN source finding、两级排序 + limit、
  scope=rule 且无启用规则早退；
- :meth:`ReviewRepository.create_review_with_findings`：review + findings
  落库、stale finding 批量标 resolved、commit 时机。

直接调 repository，不经过 orchestrator，验证 DB 语义本身。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# EncryptedString 需要 SECRET_KEY；进程启动即固定，与其它 DB 单测一致。
os.environ.setdefault("SECRET_KEY", Fernet.generate_key().decode("utf-8"))

from core.config import get_settings  # noqa: E402
from core.db import Base  # noqa: E402
from engines.types import Finding as EngineFinding  # noqa: E402
from models.finding import Finding as FindingRow  # noqa: E402
from models.negative_example import NegativeExample  # noqa: E402
from models.project import Project  # noqa: E402
from models.review import Review as ReviewRow  # noqa: E402
from repositories.negative_example import NegativeExampleRepository  # noqa: E402
from repositories.review import ReviewRepository  # noqa: E402

# conftest 导入 main 时 get_settings() 已用占位 SECRET_KEY 缓存；清掉让
# EncryptedString 拿到上面刚放进 env 的 Fernet key。
get_settings.cache_clear()

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
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


async def _seed_project(
    session_factory: async_sessionmaker[AsyncSession],
) -> Project:
    """建一个最小 Project，返回持久化后的实例。"""

    async with session_factory() as session:
        project = Project(
            name="sink-repo",
            gitlab_project_id="123",
            gitlab_access_token="tok",
            webhook_secret="sec",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


async def _add_negative_example(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    rule_id: str,
    project_id: object,  # UUID 或 None
    explanation: str | None = "exp",
    source_finding_id: object = None,
    approved_at: datetime | None = None,
) -> NegativeExample:
    """插入一条 NegativeExample。"""

    async with session_factory() as session:
        ne = NegativeExample(
            rule_id=rule_id,
            project_id=project_id,  # type: ignore[arg-type]
            code_snippet="foo()",
            explanation=explanation,
            source_finding_id=source_finding_id,  # type: ignore[arg-type]
            approved_by="admin",
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
            line_number=10,
            rule_id=rule_id,
            severity="WARNING",
            title="Bogus finding",
            description="desc",
            confidence=0.5,
        )
        session.add(finding)
        await session.commit()
        await session.refresh(finding)
        return finding


async def _list_findings(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[FindingRow]:
    async with session_factory() as session:
        result = await session.execute(select(FindingRow))
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# NegativeExampleRepository.list_approved_for_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_approved_for_history_scope_project_excludes_global(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """scope=project 只拉当前项目负例，排除其它项目与全局 (NULL) 负例。"""

    session_factory = db_session_factory
    proj_a = await _seed_project(session_factory)
    proj_b = await _seed_project(session_factory)

    await _add_negative_example(session_factory, rule_id="r-a", project_id=proj_a.id)
    await _add_negative_example(session_factory, rule_id="r-b", project_id=proj_b.id)
    await _add_negative_example(session_factory, rule_id="r-g", project_id=None)

    async with session_factory() as session:
        rows = await NegativeExampleRepository(session).list_approved_for_history(
            project_id=proj_a.id,
            active_rule_keys=[],
            scope="project",
            limit=20,
        )

    assert [ne.rule_id for ne, _ in rows] == ["r-a"]


@pytest.mark.asyncio
async def test_list_approved_for_history_scope_rule_empty_keys_returns_empty(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """scope=rule 且 active_rule_keys 为空 → 直接返回 []，不发 SQL。"""

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    await _add_negative_example(
        session_factory, rule_id="r-1", project_id=project.id
    )

    async with session_factory() as session:
        rows = await NegativeExampleRepository(session).list_approved_for_history(
            project_id=project.id,
            active_rule_keys=[],
            scope="rule",
            limit=20,
        )

    assert rows == []


@pytest.mark.asyncio
async def test_list_approved_for_history_left_join_and_order_and_limit(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """LEFT JOIN 命中 source finding；排序 approved_at DESC；limit 生效。"""

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    source = await _add_source_finding(
        session_factory, project=project, rule_id="r-1", file_path="src/foo.py"
    )
    base = datetime.now(UTC) - timedelta(days=10)
    # 命中 source finding 的负例 + 无 source 的负例，approved_at 乱序落库。
    await _add_negative_example(
        session_factory,
        rule_id="r-1",
        project_id=project.id,
        source_finding_id=source.id,
        approved_at=base,
    )
    await _add_negative_example(
        session_factory,
        rule_id="r-2",
        project_id=project.id,
        approved_at=base + timedelta(days=5),
    )
    await _add_negative_example(
        session_factory,
        rule_id="r-3",
        project_id=project.id,
        approved_at=base + timedelta(days=9),
    )

    async with session_factory() as session:
        rows = await NegativeExampleRepository(session).list_approved_for_history(
            project_id=project.id,
            active_rule_keys=[],
            scope="project",
            limit=2,
        )

    # limit=2 截断 + approved_at DESC 排序：最新的两条在前。
    assert [ne.rule_id for ne, _ in rows] == ["r-3", "r-2"]
    joined = {ne.rule_id: finding for ne, finding in rows}
    assert joined["r-3"] is None  # 无 source finding → LEFT JOIN 兜底 None
    # r-1 被截断，不出现。
    assert "r-1" not in joined


@pytest.mark.asyncio
async def test_list_approved_for_history_scope_both_union(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """scope=both 取项目负例 ∪ 规则负例（含其它项目与全局），OR 并集。"""

    session_factory = db_session_factory
    proj_a = await _seed_project(session_factory)
    proj_b = await _seed_project(session_factory)

    await _add_negative_example(
        session_factory, rule_id="rule-x", project_id=proj_a.id, explanation="A/x"
    )
    await _add_negative_example(
        session_factory, rule_id="rule-z", project_id=proj_a.id, explanation="A/z"
    )
    await _add_negative_example(
        session_factory, rule_id="rule-x", project_id=proj_b.id, explanation="B/x"
    )
    await _add_negative_example(
        session_factory, rule_id="rule-x", project_id=None, explanation="G/x"
    )

    async with session_factory() as session:
        rows = await NegativeExampleRepository(session).list_approved_for_history(
            project_id=proj_a.id,
            active_rule_keys=["rule-x"],
            scope="both",
            limit=20,
        )

    rule_ids = sorted(ne.rule_id for ne, _ in rows)
    # A 的 rule-x / rule-z（project 分支）+ B 的 rule-x 与全局 rule-x（rule 分支）。
    assert rule_ids == ["rule-x", "rule-x", "rule-x", "rule-z"]


# ---------------------------------------------------------------------------
# ReviewRepository.create_review_with_findings
# ---------------------------------------------------------------------------


def _engine_finding(
    *,
    rule_id: str = "rule-1",
    file_path: str = "app.py",
    confidence: float = 0.9,
) -> EngineFinding:
    return EngineFinding(
        file_path=file_path,
        line_number=2,
        rule_id=rule_id,
        severity="BLOCKER",
        title="hardcoded credential",
        description="detected hardcoded token",
        confidence=confidence,
    )


async def _make_review_row(
    session_factory: async_sessionmaker[AsyncSession],
    project: Project,
) -> ReviewRow:
    """构造一条未落库的 Review 行（构造属编排语义，repository 只管写）。"""

    return ReviewRow(
        id=uuid4(),
        project_id=project.id,
        mr_iid="42",
        source_branch="feature/x",
        target_branch="master",
        commit_sha="abc123",
        status="done",
        engine_used="stub-engine",
        has_blocker=True,
        finding_count=1,
        duration_ms=100,
        base_sha="b",
        parent_review_id=None,
        review_mode="full",
    )


@pytest.mark.asyncio
async def test_create_review_with_findings_persists_rows_and_commits(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """review + findings 落库，字段映射与 _persist_review 原语义一致。"""

    session_factory = db_session_factory
    project = await _seed_project(session_factory)
    review_row = await _make_review_row(session_factory, project)

    async with session_factory() as session:
        repo = ReviewRepository(session)
        await repo.create_review_with_findings(
            review=review_row,
            findings=[
                (_engine_finding(), "d1"),
                (_engine_finding(rule_id="rule-2", confidence=0.0), None),
            ],
            stale_finding_ids=[],
            resolved_in_review_id=review_row.id,
        )

    async with session_factory() as verify:
        review_rows = (await verify.execute(select(ReviewRow))).scalars().all()
        finding_rows = await _list_findings(session_factory)
    assert len(review_rows) == 1
    assert review_rows[0].id == review_row.id
    assert review_rows[0].project_id == project.id
    assert len(finding_rows) == 2
    by_rule = {row.rule_id: row for row in finding_rows}
    assert by_rule["rule-1"].gitlab_discussion_id == "d1"
    assert by_rule["rule-2"].gitlab_discussion_id is None
    assert by_rule["rule-1"].confidence == 0.9
    assert by_rule["rule-2"].confidence == 0.0
    assert by_rule["rule-1"].status == "open"
    assert by_rule["rule-1"].first_seen_review_id == review_row.id


@pytest.mark.asyncio
async def test_create_review_with_findings_marks_stale_resolved(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """stale_finding_ids 非空时批量标 resolved + resolved_in_review_id。"""

    session_factory = db_session_factory
    project = await _seed_project(session_factory)

    # 先落一条老 review + 老 finding，模拟上一轮的 open finding。
    async with session_factory() as session:
        old_review = ReviewRow(
            project_id=project.id,
            mr_iid="42",
            source_branch="feature/x",
            target_branch="master",
            commit_sha="old-sha",
            status="done",
            engine_used="stub-engine",
            has_blocker=False,
            finding_count=1,
        )
        session.add(old_review)
        await session.flush()
        old_finding = FindingRow(
            review_id=old_review.id,
            file_path="app.py",
            line_number=2,
            rule_id="rule-old",
            severity="WARNING",
            title="old finding",
            confidence=0.5,
        )
        session.add(old_finding)
        await session.commit()
        await session.refresh(old_finding)
        old_finding_id = old_finding.id

    review_row = await _make_review_row(session_factory, project)
    async with session_factory() as session:
        repo = ReviewRepository(session)
        await repo.create_review_with_findings(
            review=review_row,
            findings=[],
            stale_finding_ids=[old_finding_id],
            resolved_in_review_id=review_row.id,
        )

    async with session_factory() as verify:
        resolved = await verify.get(FindingRow, old_finding_id)
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.resolved_in_review_id == review_row.id
