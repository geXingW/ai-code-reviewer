"""负例库仓储。"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import ColumnElement, or_, select

from models.finding import Finding
from models.negative_example import NegativeExample
from repositories.base import BaseRepository


class NegativeExampleRepository(BaseRepository[NegativeExample]):
    """NegativeExample 专用查询。"""

    model = NegativeExample

    async def list_by_project(self, project_id: UUID) -> list[NegativeExample]:
        """按项目列出全部负例。"""

        stmt = select(NegativeExample).where(NegativeExample.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_approved(
        self,
        limit: int = 100,
        project_id: UUID | None = None,
    ) -> list[NegativeExample]:
        """列出已批准的负样本，按 approved_at DESC 排序。

        ``project_id`` 非 None 时只取该项目的负样本（不含 project_id 为
        NULL 的全局负例）。
        """

        stmt = select(NegativeExample).where(NegativeExample.approved_at.is_not(None))
        if project_id is not None:
            stmt = stmt.where(NegativeExample.project_id == project_id)
        stmt = stmt.order_by(NegativeExample.approved_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_approved_for_history(
        self,
        *,
        project_id: UUID,
        active_rule_keys: list[str],
        scope: str,
        limit: int,
    ) -> list[tuple[NegativeExample, Finding | None]]:
        """按 scope 拉批准负例 + LEFT JOIN source finding，排序 approved_at DESC, created_at DESC。

        供 orchestrator 的 ``_resolve_history`` 使用：``scope`` 取
        ``"project"`` / ``"rule"`` / ``"both"`` 三值——

        * ``project``：仅 ``ne.project_id == project_id``（不含 project_id NULL
          的全局负例）；
        * ``rule``：仅 ``ne.rule_id IN <active_rule_keys>``（可拉到任意项目及
          全局负例，只看规则命中）；``active_rule_keys`` 为空 → 返回 ``[]``；
        * ``both``：上述两者的 OR 并集，重复行由调用方按 id 去重。

        LEFT OUTER JOIN 兜底 ``source_finding_id`` 指向的 finding 已被 SET NULL
        的情况，此时元组第二项为 ``None``。排序用 ``approved_at DESC,
        created_at DESC`` 两级排序兼容 MySQL / PG（NULLS LAST 语法不一致）。

        Args:
            project_id: Project 主键 UUID（DB 主键，非 gitlab project_id）。
            active_rule_keys: 当前启用规则的 rule_id 集合；scope=project 时用不到。
            scope: 圈选范围，见上。
            limit: 返回条数上限。

        Returns:
            ``(NegativeExample, Finding | None)`` 元组列表，按两级排序取前 ``limit`` 条。
        """

        # 组装 scope 分支的 WHERE 子句。project=only 项目负例；rule=只按规则命中；
        # both=OR 并集。scope=rule 且没有启用规则时不可能命中任何负例，早退。
        where_clauses: list[ColumnElement[bool]] = []
        if scope in ("project", "both"):
            where_clauses.append(NegativeExample.project_id == project_id)
        if scope in ("rule", "both"):
            if active_rule_keys:
                where_clauses.append(NegativeExample.rule_id.in_(active_rule_keys))
            elif scope == "rule":
                # 无启用规则 → 按规则维度什么都拉不到，直接 return。
                return []
        if not where_clauses:
            # 极端保险：不该发生，构造保守空结果。
            return []
        combined_where = where_clauses[0] if len(where_clauses) == 1 else or_(*where_clauses)

        # LEFT OUTER JOIN 兜底 source finding 已被 SET NULL 的情况。
        stmt = (
            select(NegativeExample, Finding)
            .select_from(NegativeExample)
            .outerjoin(Finding, Finding.id == NegativeExample.source_finding_id)
            .where(combined_where)
            # NULLS LAST 用两级排序兼容 MySQL / PG：approved_at 为 NULL 时
            # 由 created_at 兜底排后面（近期新落库的靠前）。
            .order_by(
                NegativeExample.approved_at.desc(),
                NegativeExample.created_at.desc(),
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        # LEFT OUTER JOIN 的第二列在 mypy 眼里非 Optional；实际 SET NULL 后就是 None。
        return list(cast("list[tuple[NegativeExample, Finding | None]]", result.all()))
