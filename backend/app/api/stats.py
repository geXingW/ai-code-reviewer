"""Aggregate statistics API endpoints for the operator dashboard.

本模块提供只读聚合接口，服务前端「统计」页：
- overview：全局 KPI（review / finding / blocker / 平均耗时 / 活跃项目 / FP 分布 /
  engine / provider / status 分组）。
- rules：规则命中榜 Top N，含误报率。
- projects：项目活跃度 Top N，含最近一次审查时间。
- categories：finding.category 分布及占比。
- timeseries：近 N 天按日 review / finding / blocker 数（连续时间轴，缺失填 0）。

关键工程约束（与 CLAUDE.md 一致）：
- 所有 review 相关聚合都排除 ``lifecycle_event NOT NULL`` 的 MR 生命周期记账
  记录，避免重复计数。
- 聚合一律在 DB 侧完成（``group_by`` + ``func.count/avg/max``），禁止 Python 端
  遍历 review / finding。
- 时间基线：``since = datetime.now(UTC) - timedelta(days=days)``。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.api.admin import _require_admin_auth
from app.core.db import DbSession
from app.models.finding import Finding
from app.models.project import Project
from app.models.review import Review
from app.models.rule import Rule
from app.schemas._datetime import AwareDatetime

router = APIRouter(
    prefix="/api/stats",
    tags=["stats"],
    dependencies=[Depends(_require_admin_auth)],
)


# ---------------- Response schemas ----------------


class EngineUsageRead(BaseModel):
    """按 engine_used 分组的使用计数。"""

    engine: str
    count: int


class ProviderUsageRead(BaseModel):
    """按 provider_used 分组的使用计数。"""

    provider: str
    count: int


class StatusBreakdownRead(BaseModel):
    """按 review.status 分组的计数。"""

    status: str
    count: int


class StatsOverviewRead(BaseModel):
    """全局 KPI 概览。"""

    model_config = ConfigDict(from_attributes=True)

    days: int
    since: AwareDatetime
    total_reviews: int
    total_findings: int
    total_blockers: int
    total_resolved: int
    avg_duration_ms: int | None
    active_projects: int
    fp_pending: int
    fp_confirmed: int
    fp_rejected: int
    engine_usage: list[EngineUsageRead]
    provider_usage: list[ProviderUsageRead]
    status_breakdown: list[StatusBreakdownRead]


class RuleStatsRead(BaseModel):
    """规则命中榜单条。"""

    rule_id: str
    title: str | None
    severity_default: str | None
    category_default: str | None
    finding_count: int
    blocker_count: int
    projects_hit: int
    fp_confirmed: int
    fp_rejected: int
    fp_pending: int
    fp_rate: float
    resolved_count: int


class ProjectStatsRead(BaseModel):
    """项目活跃度单条。"""

    project_id: UUID
    project_name: str
    review_count: int
    finding_count: int
    blocker_count: int
    fp_confirmed: int
    avg_duration_ms: int | None
    last_reviewed_at: AwareDatetime | None


class CategoryStatsRead(BaseModel):
    """分类分布单条。"""

    category: str
    count: int
    percentage: float


class TimeseriesPointRead(BaseModel):
    """时间趋势单日数据点。"""

    date: str
    review_count: int
    finding_count: int
    blocker_count: int


# ---------------- 内部工具 ----------------


def _since(days: int) -> datetime:
    """按 UTC now 反推 ``days`` 天前作为聚合窗口起点。"""

    return datetime.now(UTC) - timedelta(days=days)


def _sum_when(expr: ColumnElement[bool]) -> ColumnElement[int]:
    """跨方言的『条件计数』：``SUM(CASE WHEN expr THEN 1 ELSE 0 END)``。

    直接对布尔列 ``cast(x == y, Integer)`` 在部分方言（如 SQLite 的
    某些 driver）行为不一致；用 ``CASE WHEN`` 一定保底为整数。
    """

    return func.coalesce(func.sum(case((expr, 1), else_=0)), 0)


# ---------------- Endpoints ----------------


@router.get("/overview", response_model=StatsOverviewRead)
async def stats_overview(
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> StatsOverviewRead:
    """全局 KPI 聚合：近 ``days`` 天数据窗口内的核心指标。"""

    since = _since(days)
    return await _build_overview(db, days=days, since=since)


async def _build_overview(
    db: AsyncSession,
    *,
    days: int,
    since: datetime,
) -> StatsOverviewRead:
    """把 5 条聚合 SQL 组装成 overview 响应。"""

    # 1. review 维度：总数 / 平均耗时 / 活跃项目数（排除 lifecycle）。
    review_stmt = select(
        func.count(Review.id),
        func.avg(Review.duration_ms),
        func.count(func.distinct(Review.project_id)),
    ).where(
        Review.created_at >= since,
        Review.lifecycle_event.is_(None),
    )
    total_reviews, avg_duration, active_projects = (await db.execute(review_stmt)).one()

    # 2. finding 维度：总数 / BLOCKER / resolved / fp_* 单次扫描。
    finding_stmt = select(
        func.count(Finding.id).label("total"),
        _sum_when(Finding.severity == "BLOCKER").label("blockers"),
        _sum_when(Finding.status == "resolved").label("resolved"),
        _sum_when(Finding.fp_status == "PENDING").label("fp_pending"),
        _sum_when(Finding.fp_status == "CONFIRMED").label("fp_confirmed"),
        _sum_when(Finding.fp_status == "REJECTED").label("fp_rejected"),
    ).where(Finding.created_at >= since)
    finding_row = (await db.execute(finding_stmt)).one()

    # 3. engine_used 分组（NULL → 'unknown'）。
    engine_col = func.coalesce(Review.engine_used, "unknown").label("engine")
    engine_stmt = (
        select(engine_col, func.count(Review.id))
        .where(Review.created_at >= since, Review.lifecycle_event.is_(None))
        .group_by(engine_col)
        .order_by(func.count(Review.id).desc())
    )
    engine_rows = (await db.execute(engine_stmt)).all()

    # 4. provider_used 分组。
    provider_col = func.coalesce(Review.provider_used, "unknown").label("provider")
    provider_stmt = (
        select(provider_col, func.count(Review.id))
        .where(Review.created_at >= since, Review.lifecycle_event.is_(None))
        .group_by(provider_col)
        .order_by(func.count(Review.id).desc())
    )
    provider_rows = (await db.execute(provider_stmt)).all()

    # 5. status 分组。
    status_stmt = (
        select(Review.status, func.count(Review.id))
        .where(Review.created_at >= since, Review.lifecycle_event.is_(None))
        .group_by(Review.status)
        .order_by(func.count(Review.id).desc())
    )
    status_rows = (await db.execute(status_stmt)).all()

    avg_duration_int: int | None = (
        int(round(float(avg_duration))) if avg_duration is not None else None
    )

    return StatsOverviewRead(
        days=days,
        since=since,
        total_reviews=int(total_reviews or 0),
        total_findings=int(finding_row.total or 0),
        total_blockers=int(finding_row.blockers or 0),
        total_resolved=int(finding_row.resolved or 0),
        avg_duration_ms=avg_duration_int,
        active_projects=int(active_projects or 0),
        fp_pending=int(finding_row.fp_pending or 0),
        fp_confirmed=int(finding_row.fp_confirmed or 0),
        fp_rejected=int(finding_row.fp_rejected or 0),
        engine_usage=[EngineUsageRead(engine=str(r[0]), count=int(r[1])) for r in engine_rows],
        provider_usage=[
            ProviderUsageRead(provider=str(r[0]), count=int(r[1])) for r in provider_rows
        ],
        status_breakdown=[
            StatusBreakdownRead(status=str(r[0]), count=int(r[1])) for r in status_rows
        ],
    )


@router.get("/rules", response_model=list[RuleStatsRead])
async def stats_rules(
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RuleStatsRead]:
    """规则命中榜：按 finding_count 降序。规则被删的 finding 走 LEFT JOIN 保留。"""

    since = _since(days)
    # 聚合走 finding 主表：Review 内连接用来数 DISTINCT project_id 并排除
    # lifecycle；Rule LEFT JOIN 仅拿展示字段，规则被删时 title 允许 NULL。
    stmt = (
        select(
            Finding.rule_id.label("rule_id"),
            func.max(Rule.title).label("title"),
            func.max(Rule.severity_default).label("severity_default"),
            func.max(Rule.category_default).label("category_default"),
            func.count(Finding.id).label("finding_count"),
            _sum_when(Finding.severity == "BLOCKER").label("blocker_count"),
            func.count(func.distinct(Review.project_id)).label("projects_hit"),
            _sum_when(Finding.fp_status == "CONFIRMED").label("fp_confirmed"),
            _sum_when(Finding.fp_status == "REJECTED").label("fp_rejected"),
            _sum_when(Finding.fp_status == "PENDING").label("fp_pending"),
            _sum_when(Finding.status == "resolved").label("resolved_count"),
        )
        .join(Review, Finding.review_id == Review.id)
        .outerjoin(Rule, Rule.rule_id == Finding.rule_id)
        .where(Finding.created_at >= since, Review.lifecycle_event.is_(None))
        .group_by(Finding.rule_id)
        .order_by(func.count(Finding.id).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    result: list[RuleStatsRead] = []
    for row in rows:
        finding_count = int(row.finding_count or 0)
        fp_confirmed = int(row.fp_confirmed or 0)
        # fp_rate 分母为 0 兜底 0.0；四舍五入 4 位。
        fp_rate = round(fp_confirmed / finding_count, 4) if finding_count > 0 else 0.0
        result.append(
            RuleStatsRead(
                rule_id=str(row.rule_id),
                title=row.title,
                severity_default=row.severity_default,
                category_default=row.category_default,
                finding_count=finding_count,
                blocker_count=int(row.blocker_count or 0),
                projects_hit=int(row.projects_hit or 0),
                fp_confirmed=fp_confirmed,
                fp_rejected=int(row.fp_rejected or 0),
                fp_pending=int(row.fp_pending or 0),
                fp_rate=fp_rate,
                resolved_count=int(row.resolved_count or 0),
            )
        )
    return result


@router.get("/projects", response_model=list[ProjectStatsRead])
async def stats_projects(
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ProjectStatsRead]:
    """项目活跃度：按 review_count 降序。排除 lifecycle_event 记录。"""

    since = _since(days)

    # review 侧聚合：review_count / avg_duration / last_reviewed_at。
    review_agg = (
        select(
            Review.project_id.label("project_id"),
            func.count(Review.id).label("review_count"),
            func.avg(Review.duration_ms).label("avg_duration_ms"),
            func.max(Review.created_at).label("last_reviewed_at"),
        )
        .where(Review.created_at >= since, Review.lifecycle_event.is_(None))
        .group_by(Review.project_id)
        .subquery()
    )

    # finding 侧聚合：finding_count / blocker_count / fp_confirmed。
    finding_agg = (
        select(
            Review.project_id.label("project_id"),
            func.count(Finding.id).label("finding_count"),
            _sum_when(Finding.severity == "BLOCKER").label("blocker_count"),
            _sum_when(Finding.fp_status == "CONFIRMED").label("fp_confirmed"),
        )
        .join(Review, Finding.review_id == Review.id)
        .where(Finding.created_at >= since, Review.lifecycle_event.is_(None))
        .group_by(Review.project_id)
        .subquery()
    )

    stmt = (
        select(
            Project.id,
            Project.name,
            func.coalesce(review_agg.c.review_count, 0),
            func.coalesce(finding_agg.c.finding_count, 0),
            func.coalesce(finding_agg.c.blocker_count, 0),
            func.coalesce(finding_agg.c.fp_confirmed, 0),
            review_agg.c.avg_duration_ms,
            review_agg.c.last_reviewed_at,
        )
        .join(review_agg, review_agg.c.project_id == Project.id)
        .outerjoin(finding_agg, finding_agg.c.project_id == Project.id)
        .order_by(review_agg.c.review_count.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    result: list[ProjectStatsRead] = []
    for row in rows:
        avg_duration = row[6]
        avg_int = int(round(float(avg_duration))) if avg_duration is not None else None
        result.append(
            ProjectStatsRead(
                project_id=row[0],
                project_name=str(row[1]),
                review_count=int(row[2] or 0),
                finding_count=int(row[3] or 0),
                blocker_count=int(row[4] or 0),
                fp_confirmed=int(row[5] or 0),
                avg_duration_ms=avg_int,
                last_reviewed_at=row[7],
            )
        )
    return result


@router.get("/categories", response_model=list[CategoryStatsRead])
async def stats_categories(
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> list[CategoryStatsRead]:
    """按 finding.category 分组统计；缺失归入 'other'，返回按 count 降序。"""

    since = _since(days)
    category_col = func.coalesce(Finding.category, "other").label("category")
    stmt = (
        select(category_col, func.count(Finding.id))
        .join(Review, Finding.review_id == Review.id)
        .where(Finding.created_at >= since, Review.lifecycle_event.is_(None))
        .group_by(category_col)
        .order_by(func.count(Finding.id).desc())
    )
    rows = (await db.execute(stmt)).all()
    total = sum(int(r[1]) for r in rows)
    result: list[CategoryStatsRead] = []
    for row in rows:
        count = int(row[1])
        # percentage 分母 0 兜底；四舍五入 4 位以匹配前端 pill 展示精度。
        percentage = round(count / total, 4) if total > 0 else 0.0
        result.append(
            CategoryStatsRead(
                category=str(row[0]),
                count=count,
                percentage=percentage,
            )
        )
    return result


@router.get("/timeseries", response_model=list[TimeseriesPointRead])
async def stats_timeseries(
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> list[TimeseriesPointRead]:
    """按天聚合近 ``days`` 天的 review / finding / blocker 数；缺失日期填 0。"""

    since = _since(days)

    # review 侧按天聚合。func.date() 在 PG / MySQL / SQLite 均可用。
    review_day = func.date(Review.created_at).label("day")
    review_stmt = (
        select(review_day, func.count(Review.id))
        .where(Review.created_at >= since, Review.lifecycle_event.is_(None))
        .group_by(review_day)
    )
    review_map: dict[str, int] = {
        _day_str(row[0]): int(row[1]) for row in (await db.execute(review_stmt)).all()
    }

    finding_day = func.date(Finding.created_at).label("day")
    finding_stmt = (
        select(
            finding_day,
            func.count(Finding.id),
            _sum_when(Finding.severity == "BLOCKER"),
        )
        .join(Review, Finding.review_id == Review.id)
        .where(Finding.created_at >= since, Review.lifecycle_event.is_(None))
        .group_by(finding_day)
    )
    finding_map: dict[str, tuple[int, int]] = {
        _day_str(row[0]): (int(row[1]), int(row[2] or 0))
        for row in (await db.execute(finding_stmt)).all()
    }

    # 生成连续日期轴：从 since 的日期起到今日（UTC）。
    today = datetime.now(UTC).date()
    start_day = since.date()
    result: list[TimeseriesPointRead] = []
    current = start_day
    while current <= today:
        key = current.isoformat()
        review_count = review_map.get(key, 0)
        finding_count, blocker_count = finding_map.get(key, (0, 0))
        result.append(
            TimeseriesPointRead(
                date=key,
                review_count=review_count,
                finding_count=finding_count,
                blocker_count=blocker_count,
            )
        )
        current = current + timedelta(days=1)
    return result


def _day_str(value: date | datetime | str | None) -> str:
    """把 func.date 返回值统一格式化为 ``YYYY-MM-DD`` 字符串。

    - PostgreSQL 返回 ``date``；
    - MySQL 通过 aiomysql 返回 ``datetime`` 或 ``date``；
    - SQLite 返回 ``str``。
    """

    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
