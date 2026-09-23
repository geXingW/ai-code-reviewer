"""Resolve enabled project rules for a review event."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from engines import RuleSpec
from repositories.project import ProjectRepository
from services.review_orchestration.events import _EventLike

if TYPE_CHECKING:
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)


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
