"""Resolve approved negative-example history for a review event."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from core.config import get_settings
from engines import RuleSpec
from engines.types import ReviewHistoryItem
from repositories.negative_example import NegativeExampleRepository
from repositories.project import ProjectRepository
from services.review_orchestration.events import _EventLike

if TYPE_CHECKING:
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)


async def _resolve_history(
    event: _EventLike,
    rules: list[RuleSpec],
    *,
    session_factory: SessionFactory | None,
) -> list[ReviewHistoryItem]:
    """从 DB 拉批准过的 NegativeExample 转成 ReviewHistoryItem 列表。

    用于把历史误报"喂"回 prompt，让引擎既能在 prompt 里读到"不要再报这些"，
    又能在 engine 端的 ``_matches_false_positive_history`` 硬过滤阶段兜底
    drop 掉相似 finding。

    - ``settings.llm_history_max_items`` 为 0 → 直接短路返回空列表，甚至
      不打开 DB session。
    - ``session_factory`` 为 None → 保留旧 MVP 行为，返回空列表。
    - Project 未在管理后台注册 → 返回空列表（拿不到 project_id 就不谈范围）。
    - 按 ``settings.llm_history_scope`` 圈选（WHERE 组装 / JOIN / 排序 / limit
      已下沉 :meth:`NegativeExampleRepository.list_approved_for_history`）：
      * ``project``：仅当前项目负例；
      * ``rule``：仅当前启用规则命中的负例（含全局负例）；
      * ``both``：上述两者的 OR 并集，Python 层按 id 去重。
    - LEFT OUTER JOIN 兜底：``source_finding_id`` 指向的 finding 可能被 SET NULL，
      此时 file_path / title / description / line_number 都拿不到，
      走 "(unknown)" + 兜底标题让 engine 侧的硬过滤跳过它（file_path 不同不匹配），
      只留 prompt 展示 explanation。
    - 排序：``approved_at DESC, created_at DESC`` —— NULLS LAST 语义在 MySQL/PG
      之间语法不一致，改用两级排序，让"最近批准且落库最晚"的负例排前。
    - DB 任何异常都被 catch，logger.warning + 返回空列表，不阻断主流程。
    """

    settings = get_settings()
    limit = settings.llm_history_max_items
    if limit <= 0:
        # 用户显式关闭反哺；不查 DB，直接短路。
        return []
    if session_factory is None:
        return []
    scope = settings.llm_history_scope
    # scope=rule / both 需要"当前启用规则的 rule_id 集合"；scope=project 用不到。
    # 只取 enabled 的规则（_resolve_rules 已经过滤了 enabled=False 的 link 与 rule）。
    active_rule_keys = [rule.rule_id for rule in rules if rule.enabled]

    try:
        async with session_factory() as session:
            project_repo = ProjectRepository(session)
            project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
            if project is None:
                return []

            rows = await NegativeExampleRepository(session).list_approved_for_history(
                project_id=project.id,
                active_rule_keys=active_rule_keys,
                scope=scope,
                limit=limit,
            )

            history: list[ReviewHistoryItem] = []
            seen_ids: set[UUID] = set()
            for ne, finding_row in rows:
                # scope=both 时 SQL 层通过 OR 可能重复；用 id 去重。
                if ne.id in seen_ids:
                    continue
                seen_ids.add(ne.id)
                if finding_row is not None:
                    file_path = finding_row.file_path
                    line_number = finding_row.line_number
                    title = finding_row.title
                    description = finding_row.description
                else:
                    # source finding 已被删；file_path 用 "(unknown)" 兜底，
                    # 让 engine 侧硬过滤（rule_id + file_path）跳过它，只走
                    # prompt 展示 explanation。
                    file_path = "(unknown)"
                    line_number = None
                    title = f"Historical false-positive for {ne.rule_id}"
                    description = None
                confirmed_at = (
                    ne.approved_at.isoformat()
                    if ne.approved_at is not None
                    else ne.created_at.isoformat()
                )
                history.append(
                    ReviewHistoryItem(
                        rule_id=ne.rule_id,
                        file_path=file_path,
                        line_number=line_number,
                        title=title,
                        description=description,
                        review_note=(
                            ne.explanation
                            if ne.explanation
                            else "Confirmed false-positive; do not re-report."
                        ),
                        confirmed_at=confirmed_at,
                    )
                )

            if history:
                logger.info(
                    "negative examples injected",
                    extra={
                        "count": len(history),
                        "gitlab_project_id": event.project_id,
                        "scope": scope,
                    },
                )
            else:
                logger.debug(
                    "no negative examples matched for review",
                    extra={
                        "gitlab_project_id": event.project_id,
                        "scope": scope,
                    },
                )
            return history
    except Exception:
        # 落库失败不影响主流程：任何 DB / ORM / 校验异常都吞掉，返回空历史。
        logger.warning(
            "failed to resolve history, review continues without negative examples",
            exc_info=True,
            extra={"gitlab_project_id": event.project_id},
        )
        return []
