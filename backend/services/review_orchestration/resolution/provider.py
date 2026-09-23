"""Resolve the provider config for a review event."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from engines import ProviderConfig
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
