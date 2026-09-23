"""Resolve provider / rules / history context for a review event."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from core.config import get_settings
from engines import ProviderConfig, RuleSpec
from engines.types import ReviewHistoryItem
from models.finding import Finding as FindingRow
from models.negative_example import NegativeExample
from repositories.project import ProjectRepository
from repositories.provider import ProviderRepository
from services.review_orchestration.events import _EventLike

if TYPE_CHECKING:
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)


async def _resolve_provider(
    event: _EventLike,
    *,
    session_factory: SessionFactory | None,
) -> ProviderConfig | None:
    """按 GitLab project_id 查 Project 关联的 Provider，转成 ``ProviderConfig``。

    为 orchestrator 的引擎调用注入 provider 配置。查不到 Project、Project 未
    关联 provider_id、Provider 已删或已禁用、DB / 解密异常，一律返回 ``None``
    让 llm-direct 引擎优雅退化（跳过评审、返回空 findings），**绝不能阻断
    主流程**。

    Args:
        event: 归一化后的 MR 事件。

    Returns:
        解密后的 ``ProviderConfig``；无法解析时 ``None``。
    """

    if session_factory is None:
        return None
    try:
        async with session_factory() as session:
            project_repo = ProjectRepository(session)
            project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
            if project is None or project.provider_id is None:
                return None
            provider_repo = ProviderRepository(session)
            provider = await provider_repo.get(project.provider_id)
            if provider is None or not provider.enabled:
                logger.warning(
                    "provider missing or disabled; llm-direct will skip",
                    extra={
                        "gitlab_project_id": event.project_id,
                        "provider_id": str(project.provider_id),
                    },
                )
                return None
            # Provider.api_key 是 EncryptedString，读出时已自动解密。
            return ProviderConfig(
                provider_id=provider.id,
                provider_type=provider.protocol,
                base_url=provider.base_url,
                model=provider.model,
                api_key=provider.api_key,
                temperature=provider.temperature,
                max_tokens=provider.max_tokens,
                extra=provider.extra_headers or {},
            )
    except SQLAlchemyError:
        logger.exception(
            "provider resolution failed",
            extra={"gitlab_project_id": event.project_id},
        )
        return None
    except Exception:
        # 解密失败 / Fernet key 不匹配等异常也吞掉，走 llm-direct skip 分支。
        logger.exception(
            "provider resolution failed with unexpected error",
            extra={"gitlab_project_id": event.project_id},
        )
        return None


async def resolve_rules(
    event: _EventLike,
    *,
    session_factory: SessionFactory | None,
) -> list[RuleSpec]:
    """从 DB 查项目已启用的规则并投影为 ``RuleSpec`` 列表。

    走 ``Project.project_rules`` selectin 关系，只保留 ProjectRule.enabled=True
    且底层 Rule.enabled=True 的项；severity 优先取 ProjectRule.severity_override，
    否则用 Rule.severity_default，构造成 ``RuleSpec`` 交给引擎放入 prompt。

    - ``session_factory`` 为 None、Project 未注册、DB 异常：一律返回空列表，
      让引擎走无规则路径（llm-direct 目前会打印 "No project-specific rules
      were supplied. Focus on correctness and security."）。绝不能阻断主流程。

    Args:
        event: 归一化后的 MR 事件。

    Returns:
        投影后的 ``RuleSpec`` 列表；查询失败或无规则时返回空列表。
    """

    if session_factory is None:
        return []
    try:
        async with session_factory() as session:
            project_repo = ProjectRepository(session)
            project = await project_repo.get_by_gitlab_project_id(str(event.project_id))
            if project is None:
                return []
            specs: list[RuleSpec] = []
            for link in project.project_rules:
                if not link.enabled:
                    continue
                rule = link.rule
                if rule is None or not rule.enabled:
                    continue
                severity = link.severity_override or rule.severity_default
                # 规范化到 Literal["INFO","WARNING","BLOCKER"]；未知值降级为 WARNING
                severity_upper = severity.upper() if isinstance(severity, str) else "WARNING"
                if severity_upper not in ("INFO", "WARNING", "BLOCKER"):
                    severity_upper = "WARNING"
                specs.append(
                    RuleSpec(
                        id=rule.id,
                        rule_id=rule.rule_id,
                        title=rule.title,
                        description=rule.prompt_snippet,
                        severity=severity_upper,
                        # rule.category_default 可能为 None（老数据未回填）；
                        # 让 engine 侧的 _format_rules 用 'other' 兜底以对齐
                        # FindingCategory 枚举，'general' 不在合法值内。
                        category=rule.category_default,
                        enabled=True,
                    )
                )
            return specs
    except SQLAlchemyError:
        logger.exception(
            "rules resolution failed",
            extra={"gitlab_project_id": event.project_id},
        )
        return []
    except Exception:
        logger.exception(
            "rules resolution failed with unexpected error",
            extra={"gitlab_project_id": event.project_id},
        )
        return []


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
    - 按 ``settings.llm_history_scope`` 构造 WHERE：
      * ``project``：仅 ``ne.project_id == project.id``（不含 project_id NULL 的全局负例）；
      * ``rule``：仅 ``ne.rule_id IN <当前启用规则 rule_id 集合>``（可拉到任意项目
        及全局负例，只看规则命中）；
      * ``both``：上述两者的 OR 并集，SQL 层通过 id DISTINCT 去重。
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

            # 组装 scope 分支的 WHERE 子句。project=only 项目负例；rule=只按规则命中；
            # both=OR 并集。scope=rule 且没有启用规则时不可能命中任何负例，早退。
            where_clauses = []
            if scope in ("project", "both"):
                where_clauses.append(NegativeExample.project_id == project.id)
            if scope in ("rule", "both"):
                if active_rule_keys:
                    where_clauses.append(NegativeExample.rule_id.in_(active_rule_keys))
                elif scope == "rule":
                    # 无启用规则 → 按规则维度什么都拉不到，直接 return。
                    return []
            if not where_clauses:
                # 极端保险：不该发生，构造保守空结果。
                return []
            combined_where = (
                where_clauses[0] if len(where_clauses) == 1 else or_(*where_clauses)
            )

            # LEFT OUTER JOIN 兜底 source finding 已被 SET NULL 的情况。
            stmt = (
                select(NegativeExample, FindingRow)
                .select_from(NegativeExample)
                .outerjoin(FindingRow, FindingRow.id == NegativeExample.source_finding_id)
                .where(combined_where)
                # NULLS LAST 用两级排序兼容 MySQL / PG：approved_at 为 NULL 时
                # 由 created_at 兜底排后面（近期新落库的靠前）。
                .order_by(
                    NegativeExample.approved_at.desc(),
                    NegativeExample.created_at.desc(),
                )
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.all()

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
