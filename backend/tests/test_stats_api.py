"""Tests for the aggregate statistics API (/api/stats/*)."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config, db
from app.core.db import Base, get_db
from app.main import create_app
from app.models.finding import Finding
from app.models.project import Project
from app.models.review import Review
from app.models.rule import Rule

# 同其他 e2e 用例：默认走本机 PG，允许 CI 覆盖 DATABASE_URL。
TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


@pytest_asyncio.fixture
async def stats_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[AsyncClient, async_sessionmaker[AsyncSession]], None]:
    """FastAPI client + session factory：先重建 schema，再登录拿到 bearer。"""

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode("utf-8"))
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()

    test_engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        login_response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        assert login_response.status_code == 200
        client.headers.update(
            {"Authorization": f"Bearer {login_response.json()['access_token']}"}
        )
        yield client, session_factory

    app.dependency_overrides.clear()
    await test_engine.dispose()
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()


async def _seed_baseline(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, UUID]:
    """种子基线数据：3 个项目、若干 review / finding，覆盖 lifecycle / NULL 分组。"""

    async with session_factory() as session:
        proj_a = Project(
            name="alpha",
            gitlab_project_id="1",
            gitlab_access_token="tok",
            webhook_secret="sec",
        )
        proj_b = Project(
            name="beta",
            gitlab_project_id="2",
            gitlab_access_token="tok",
            webhook_secret="sec",
        )
        proj_c = Project(
            name="gamma",
            gitlab_project_id="3",
            gitlab_access_token="tok",
            webhook_secret="sec",
        )
        session.add_all([proj_a, proj_b, proj_c])
        await session.flush()

        now = datetime.now(UTC)

        # alpha：2 条普通 review + 3 条 finding。engine=llm-direct provider=ark。
        r_a1 = Review(
            project_id=proj_a.id,
            mr_iid="1",
            source_branch="f/1",
            target_branch="master",
            commit_sha="c1",
            status="done",
            engine_used="llm-direct",
            provider_used="ark",
            has_blocker=True,
            finding_count=2,
            duration_ms=1000,
        )
        r_a2 = Review(
            project_id=proj_a.id,
            mr_iid="2",
            source_branch="f/2",
            target_branch="master",
            commit_sha="c2",
            status="done",
            # engine_used / provider_used 缺失 → 覆盖 NULL → 'unknown' 归类。
            engine_used=None,
            provider_used=None,
            has_blocker=False,
            finding_count=1,
            # duration_ms=None：验证 avg 排除 NULL 分子。
            duration_ms=None,
        )
        session.add_all([r_a1, r_a2])
        await session.flush()

        # beta：1 条 finalized review + 1 条 lifecycle 记账 review。
        r_b1 = Review(
            project_id=proj_b.id,
            mr_iid="3",
            source_branch="f/3",
            target_branch="master",
            commit_sha="c3",
            status="failed",
            engine_used="llm-direct",
            provider_used="ark",
            has_blocker=False,
            finding_count=0,
            duration_ms=2000,
        )
        r_b_lifecycle = Review(
            project_id=proj_b.id,
            mr_iid="3",
            source_branch="f/3",
            target_branch="master",
            commit_sha="c3",
            status="done",
            engine_used="llm-direct",
            provider_used="ark",
            has_blocker=False,
            finding_count=0,
            duration_ms=100,
            lifecycle_event="mr_merged",
        )
        session.add_all([r_b1, r_b_lifecycle])
        await session.flush()

        # gamma：只有 lifecycle 记账 review，不应计入活跃项目。
        r_c_lifecycle = Review(
            project_id=proj_c.id,
            mr_iid="9",
            source_branch="f/9",
            target_branch="master",
            commit_sha="c9",
            status="done",
            engine_used="llm-direct",
            provider_used="ark",
            has_blocker=False,
            finding_count=0,
            duration_ms=None,
            lifecycle_event="mr_closed",
        )
        session.add(r_c_lifecycle)
        await session.flush()

        # Findings：alpha r_a1 挂 2 条（含 BLOCKER），r_a2 挂 1 条 resolved。
        session.add_all(
            [
                Finding(
                    review_id=r_a1.id,
                    file_path="a.py",
                    line_number=1,
                    rule_id="known-rule",
                    severity="BLOCKER",
                    title="issue-1",
                    category="security",
                    fp_status="CONFIRMED",
                    status="open",
                ),
                Finding(
                    review_id=r_a1.id,
                    file_path="a.py",
                    line_number=2,
                    rule_id="known-rule",
                    severity="WARNING",
                    title="issue-2",
                    category=None,  # 覆盖 category NULL → 'other'
                    fp_status="PENDING",
                    status="open",
                ),
                Finding(
                    review_id=r_a2.id,
                    file_path="b.py",
                    line_number=3,
                    rule_id="ghost-rule",  # 规则库无此条 → LEFT JOIN 应保留
                    severity="INFO",
                    title="issue-3",
                    category="bug",
                    fp_status="REJECTED",
                    status="resolved",
                ),
            ]
        )

        # 规则库：只登记 known-rule；ghost-rule 故意不建。
        session.add(
            Rule(
                rule_id="known-rule",
                title="Known Rule",
                prompt_snippet="prompt",
                severity_default="WARNING",
                category_default="security",
                languages=[],
                path_patterns=[],
            )
        )

        # 老数据：40 天前的一条 review，落在 7 天窗口外。
        stale = Review(
            project_id=proj_a.id,
            mr_iid="99",
            source_branch="f/99",
            target_branch="master",
            commit_sha="c99",
            status="done",
            engine_used="llm-direct",
            provider_used="ark",
            has_blocker=False,
            finding_count=0,
            duration_ms=500,
        )
        session.add(stale)
        await session.flush()
        # 手工回填 created_at（默认列由 default= 生成，flush 时用 now；覆盖它）。
        stale.created_at = now - timedelta(days=40)
        session.add(stale)

        await session.commit()

        return {
            "alpha": proj_a.id,
            "beta": proj_b.id,
            "gamma": proj_c.id,
            "review_alpha_1": r_a1.id,
            "review_alpha_2": r_a2.id,
        }


@pytest.mark.asyncio
async def test_overview_excludes_lifecycle_reviews(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """overview.total_reviews / active_projects 都要排除 lifecycle 记账。"""

    client, session_factory = stats_client
    await _seed_baseline(session_factory)

    response = await client.get("/api/stats/overview?days=30")
    assert response.status_code == 200
    body = response.json()
    # alpha r_a1/r_a2 + beta r_b1 = 3 条（lifecycle 两条不算）。
    assert body["total_reviews"] == 3
    # gamma 只有 lifecycle → 不算活跃项目；alpha + beta = 2。
    assert body["active_projects"] == 2
    # findings：BLOCKER=1，resolved=1，fp_confirmed=1 pending=1 rejected=1。
    assert body["total_findings"] == 3
    assert body["total_blockers"] == 1
    assert body["total_resolved"] == 1
    assert body["fp_confirmed"] == 1
    assert body["fp_pending"] == 1
    assert body["fp_rejected"] == 1


@pytest.mark.asyncio
async def test_overview_engine_and_provider_unknown_bucket(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """engine_used / provider_used 为 NULL 的 review 归入 'unknown'。"""

    client, session_factory = stats_client
    await _seed_baseline(session_factory)

    body = (await client.get("/api/stats/overview?days=30")).json()
    engine_map = {row["engine"]: row["count"] for row in body["engine_usage"]}
    provider_map = {row["provider"]: row["count"] for row in body["provider_usage"]}
    # r_a2 engine_used=None → unknown；其余 2 条 llm-direct。
    assert engine_map["llm-direct"] == 2
    assert engine_map["unknown"] == 1
    assert provider_map["ark"] == 2
    assert provider_map["unknown"] == 1


@pytest.mark.asyncio
async def test_overview_avg_duration_excludes_null(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """duration_ms 为 NULL 的 review 不进 avg 分子分母。"""

    client, session_factory = stats_client
    await _seed_baseline(session_factory)

    body = (await client.get("/api/stats/overview?days=30")).json()
    # r_a1=1000, r_b1=2000（r_a2 duration=None 排除），平均 1500。
    assert body["avg_duration_ms"] == 1500


@pytest.mark.asyncio
async def test_overview_days_narrow_window_drops_old_data(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """days=7 时窗口内不应包含 40 天前的老 review。"""

    client, session_factory = stats_client
    await _seed_baseline(session_factory)

    body = (await client.get("/api/stats/overview?days=7")).json()
    # 3 条最近 review 都在 7 天内；老数据无论如何应被过滤。
    assert body["total_reviews"] == 3

    # 反向断言：days=365 时也应看到那条 40 天前的记录（4 条 review）。
    body_365 = (await client.get("/api/stats/overview?days=365")).json()
    assert body_365["total_reviews"] == 4


@pytest.mark.asyncio
async def test_rules_ordering_and_left_join(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """rules 榜按 finding_count 降序 + 规则被删的 finding 依然出现。"""

    client, session_factory = stats_client
    await _seed_baseline(session_factory)

    body = (await client.get("/api/stats/rules?days=30")).json()
    assert isinstance(body, list)
    # known-rule 命中 2 条，ghost-rule 命中 1 条。
    assert body[0]["rule_id"] == "known-rule"
    assert body[0]["finding_count"] == 2
    assert body[0]["title"] == "Known Rule"
    assert body[1]["rule_id"] == "ghost-rule"
    assert body[1]["finding_count"] == 1
    # LEFT JOIN：ghost-rule 在 rules 表没有，title 应为 None。
    assert body[1]["title"] is None
    assert body[1]["severity_default"] is None
    assert body[1]["category_default"] is None


@pytest.mark.asyncio
async def test_rules_fp_rate_math(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """fp_rate = fp_confirmed / finding_count；分母 0 时兜底为 0.0。"""

    client, session_factory = stats_client
    await _seed_baseline(session_factory)

    body = (await client.get("/api/stats/rules?days=30")).json()
    by_id = {row["rule_id"]: row for row in body}
    # known-rule：2 条 finding，1 条 CONFIRMED → 0.5。
    assert by_id["known-rule"]["finding_count"] == 2
    assert by_id["known-rule"]["fp_confirmed"] == 1
    assert by_id["known-rule"]["fp_rate"] == 0.5
    # ghost-rule：1 条 finding，0 CONFIRMED → 0.0。
    assert by_id["ghost-rule"]["fp_rate"] == 0.0


@pytest.mark.asyncio
async def test_projects_ordering_and_last_reviewed_at(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """projects 按 review_count 降序 + last_reviewed_at 取最新。"""

    client, session_factory = stats_client
    ids = await _seed_baseline(session_factory)

    body = (await client.get("/api/stats/projects?days=30")).json()
    # alpha 2 条 > beta 1 条；gamma 只有 lifecycle 应完全不在列表里。
    assert [row["project_name"] for row in body] == ["alpha", "beta"]
    alpha = body[0]
    assert alpha["review_count"] == 2
    assert alpha["finding_count"] == 3
    assert alpha["blocker_count"] == 1
    assert alpha["fp_confirmed"] == 1
    # last_reviewed_at 存在且带时区。
    assert alpha["last_reviewed_at"] is not None
    assert alpha["last_reviewed_at"].endswith("+00:00")
    # 排除 gamma。
    names = {row["project_name"] for row in body}
    assert "gamma" not in names
    # id 一致性顺带校验。
    assert UUID(alpha["project_id"]) == ids["alpha"]


@pytest.mark.asyncio
async def test_projects_avg_duration_excludes_null(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """项目层 avg_duration_ms 与 overview 逻辑一致：排除 NULL。"""

    client, session_factory = stats_client
    await _seed_baseline(session_factory)

    body = (await client.get("/api/stats/projects?days=30")).json()
    alpha = next(row for row in body if row["project_name"] == "alpha")
    # r_a1=1000, r_a2=NULL → 平均 1000。
    assert alpha["avg_duration_ms"] == 1000


@pytest.mark.asyncio
async def test_categories_other_bucket_and_percentage(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """未打 category 归入 'other' + percentage 和 ≈ 1.0（浮点容差）。"""

    client, session_factory = stats_client
    await _seed_baseline(session_factory)

    body = (await client.get("/api/stats/categories?days=30")).json()
    total = sum(row["count"] for row in body)
    assert total == 3
    buckets = {row["category"]: row for row in body}
    assert buckets["security"]["count"] == 1
    assert buckets["bug"]["count"] == 1
    assert buckets["other"]["count"] == 1
    # percentage 求和 ≈ 1.0；每个都是 1/3 = 0.3333。
    assert abs(sum(row["percentage"] for row in body) - 1.0) < 1e-3


@pytest.mark.asyncio
async def test_timeseries_fills_missing_days_with_zero(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """timeseries 返回 days+1 个连续日期点，缺失日期填 0。"""

    client, session_factory = stats_client
    await _seed_baseline(session_factory)

    body = (await client.get("/api/stats/timeseries?days=30")).json()
    # since = now - 30 days，[since.date() .. today] = 31 天。
    assert len(body) == 31
    # 日期严格升序。
    dates = [row["date"] for row in body]
    assert dates == sorted(dates)
    # 至少有一天 review_count 是 0（其余 30 天未种子）。
    assert any(row["review_count"] == 0 for row in body)
    # 至少有一天 review_count > 0（今日有种子）。
    assert any(row["review_count"] > 0 for row in body)


@pytest.mark.asyncio
async def test_stats_endpoints_require_admin_bearer(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """未带 bearer 时所有 stats endpoint 返回 401。"""

    client, _ = stats_client
    for path in (
        "/api/stats/overview",
        "/api/stats/rules",
        "/api/stats/projects",
        "/api/stats/categories",
        "/api/stats/timeseries",
    ):
        response = await client.get(path, headers={"Authorization": ""})
        assert response.status_code == 401, path


@pytest.mark.asyncio
async def test_overview_since_uses_utc(
    stats_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    """overview.since 是 aware ISO；结构性校验避免时区回归。"""

    client, session_factory = stats_client
    await _seed_baseline(session_factory)

    body = (await client.get("/api/stats/overview?days=30")).json()
    assert body["since"].endswith("+00:00")
    assert body["days"] == 30
