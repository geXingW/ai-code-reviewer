"""提示词配置路由。

- 全局 system prompt（``/settings/global-prompt``）：对所有审查引擎生效。
- 项目级负样本提示词：按项目覆盖，支持根据已批准负样本调用 LLM 生成草稿
  （生成逻辑在 :mod:`services.prompt_generation`，路由不含业务实现）。
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin.common import (
    _commit_or_400,
    _get_or_404,
)
from api.admin.deps import (
    _require_admin_auth,
    _require_permission,
)
from core.db import DbSession
from models.project import Project
from models.provider import Provider
from repositories import (
    NegativeExampleRepository,
    ProjectNegativePromptRepository,
    ProviderRepository,
)
from schemas.global_setting import (
    GlobalPromptResponse,
    GlobalPromptUpdate,
)
from schemas.project_negative_prompt import (
    ProjectNegativePromptGenerateRequest,
    ProjectNegativePromptGenerateResponse,
    ProjectNegativePromptResponse,
    ProjectNegativePromptUpdate,
)
from services.prompt_generation import PromptGenerationError, generate_negative_prompt

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["admin"],
    dependencies=[Depends(_require_admin_auth)],
)

_GLOBAL_PROMPT_KEY = "global_system_prompt"


@router.get(
    "/settings/global-prompt",
    response_model=GlobalPromptResponse,
    dependencies=[Depends(_require_permission("page:global-prompt"))],
)
async def get_global_prompt(db: DbSession) -> GlobalPromptResponse:
    """Return the current global system prompt content.

    Returns an empty string when the setting has never been configured.
    """

    from repositories.global_setting import GlobalSettingRepository

    repo = GlobalSettingRepository(db)
    content = await repo.get_value(_GLOBAL_PROMPT_KEY, default="")
    return GlobalPromptResponse(content=content)


@router.put(
    "/settings/global-prompt",
    response_model=GlobalPromptResponse,
    dependencies=[Depends(_require_permission("page:global-prompt"))],
)
async def update_global_prompt(
    payload: GlobalPromptUpdate,
    db: DbSession,
) -> GlobalPromptResponse:
    """Update the global system prompt and return the saved value."""

    from repositories.global_setting import GlobalSettingRepository

    repo = GlobalSettingRepository(db)
    await repo.set_value(_GLOBAL_PROMPT_KEY, payload.content)
    await _commit_or_400(db, detail="Failed to save global prompt")
    return GlobalPromptResponse(content=payload.content)


@router.get(
    "/projects/{project_id}/negative-prompt",
    response_model=ProjectNegativePromptResponse,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def get_project_negative_prompt(
    project_id: UUID,
    db: DbSession,
) -> ProjectNegativePromptResponse:
    """返回项目级负样本提示词。

    未配置时返回空字符串（引擎层回退到旧的结构化负样本注入）。
    """

    await _get_or_404(db, Project, project_id, "Project")
    repo = ProjectNegativePromptRepository(db)
    content = await repo.get_content(project_id)
    example_count = await repo.count_approved_examples(project_id)
    return ProjectNegativePromptResponse(content=content, example_count=example_count)


@router.put(
    "/projects/{project_id}/negative-prompt",
    response_model=ProjectNegativePromptResponse,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def update_project_negative_prompt(
    project_id: UUID,
    payload: ProjectNegativePromptUpdate,
    db: DbSession,
) -> ProjectNegativePromptResponse:
    """更新项目级负样本提示词（有则 UPDATE 无则 INSERT）。"""

    await _get_or_404(db, Project, project_id, "Project")
    repo = ProjectNegativePromptRepository(db)
    await repo.upsert(project_id, payload.content)
    await _commit_or_400(db, detail="Failed to save project negative prompt")
    example_count = await repo.count_approved_examples(project_id)
    return ProjectNegativePromptResponse(content=payload.content, example_count=example_count)


async def _pick_generate_provider(
    db: AsyncSession,
    provider_id: UUID | None,
    project: Project,
) -> Provider:
    """选择用于生成负样本提示词的 provider，取法与评审链路一致
    （见 services/review_orchestration/resolution/provider.py）。

    - 指定 ``provider_id``：取该 provider，不存在则 404；
    - 未指定：取 ``project.provider_id`` 对应的 provider，
      未配置、不存在或已禁用则 400。
    """

    if provider_id is not None:
        return await _get_or_404(db, Provider, provider_id, "Provider")
    if project.provider_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该项目未配置 LLM Provider，请先在项目设置中选择供应商",
        )
    provider = await ProviderRepository(db).get(project.provider_id)
    if provider is None or not provider.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该项目配置的 LLM Provider 不存在或已禁用",
        )
    return provider


@router.post(
    "/projects/{project_id}/negative-prompt/generate",
    response_model=ProjectNegativePromptGenerateResponse,
    dependencies=[Depends(_require_permission("page:projects"))],
)
async def generate_project_negative_prompt(
    project_id: UUID,
    db: DbSession,
    payload: ProjectNegativePromptGenerateRequest | None = None,
) -> ProjectNegativePromptGenerateResponse:
    """根据该项目的已批准负样本调用 LLM 生成项目级负样本提示词。

    只取 ``NegativeExample.project_id == project_id`` 且已批准的样本
    （不含 project_id 为 NULL 的全局负例）。只返回生成结果，不自动保存
    （用户在前端预览、编辑后再手动保存）。
    """

    project = await _get_or_404(db, Project, project_id, "Project")

    examples = await NegativeExampleRepository(db).list_all_approved(
        limit=100,
        project_id=project_id,
    )
    if not examples:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该项目负样本库为空，无法生成",
        )
    provider = await _pick_generate_provider(
        db, payload.provider_id if payload else None, project
    )

    try:
        content = await generate_negative_prompt(examples=examples, provider=provider)
    except PromptGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"负样本提示词生成失败：{exc.detail}" if exc.detail else "负样本提示词生成失败",
        ) from exc

    return ProjectNegativePromptGenerateResponse(content=content, source_count=len(examples))
