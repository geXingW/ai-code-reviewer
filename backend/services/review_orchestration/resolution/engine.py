"""Resolve the review engine for a review event (project-level override)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from engines.registry import EngineNotFoundError, EngineRegistry
from repositories.engine import EngineRepository
from repositories.project import ProjectRepository
from services.review_orchestration.events import _EventLike

if TYPE_CHECKING:
    from services.review_orchestration.orchestrator import SessionFactory

logger = logging.getLogger(__name__)


async def _resolve_engine(
    event: _EventLike,
    *,
    session_factory: SessionFactory | None,
    registry: EngineRegistry,
    default_engine: str,
) -> str:
    """按项目配置解析本次评审实际使用的引擎名，任何失败回退 ``default_engine``。

    解析链路：``projects.engine_id`` → ``engines`` 表（``name`` 存 registry
    引擎名，``engine_type`` 只是类别值）→ registry 校验。与
    :func:`_resolve_provider` 同样的 fail-open 契约：Project 未注册、未配置
    engine_id、Engine 记录缺失 / 已禁用、名字未注册到 registry、DB / 解密
    异常，一律回退全局默认引擎并记日志，**绝不能阻断评审主流程**。

    Args:
        event: 归一化后的评审事件。
        session_factory: 异步会话工厂；``None``（MVP 兼容路径）直接用默认引擎。
        registry: 引擎注册表，用于校验配置的引擎名确实已注册可用。
        default_engine: 全局默认引擎名（``settings.default_review_engine``）。

    Returns:
        实际使用的引擎名。
    """

    if session_factory is None:
        return default_engine
    try:
        async with session_factory() as session:
            project = await ProjectRepository(session).get_by_gitlab_project_id(
                str(event.project_id)
            )
            if project is None or project.engine_id is None:
                return default_engine
            engine_cfg = await EngineRepository(session).get(project.engine_id)
            if engine_cfg is None or not engine_cfg.enabled:
                logger.warning(
                    "project engine missing or disabled; falling back to default",
                    extra={
                        "gitlab_project_id": event.project_id,
                        "engine_id": str(project.engine_id),
                    },
                )
                return default_engine
            resolved_engine = engine_cfg.name
    except SQLAlchemyError:
        logger.exception(
            "project engine resolution failed; falling back to default",
            extra={"gitlab_project_id": event.project_id},
        )
        return default_engine
    except Exception:
        # 解密失败等意外异常也吞掉，保持评审主流程可用。
        logger.exception(
            "project engine resolution failed with unexpected error; "
            "falling back to default",
            extra={"gitlab_project_id": event.project_id},
        )
        return default_engine

    # engines.name 对应 registry 引擎名；配置了未注册的名字（手写错误等）时回退。
    try:
        registry.get(resolved_engine)
    except EngineNotFoundError:
        logger.warning(
            "configured engine not registered; falling back to default",
            extra={
                "gitlab_project_id": event.project_id,
                "engine_name": resolved_engine,
            },
        )
        return default_engine
    return resolved_engine
