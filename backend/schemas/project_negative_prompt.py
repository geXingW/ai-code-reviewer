"""Pydantic schemas for project-scoped negative prompt endpoints."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ProjectNegativePromptResponse(BaseModel):
    """项目级负样本提示词响应。"""

    content: str
    example_count: int = Field(description="该项目已批准负样本数量")


class ProjectNegativePromptUpdate(BaseModel):
    """更新项目级负样本提示词请求。"""

    content: str = Field(min_length=0, max_length=50000)


class ProjectNegativePromptGenerateRequest(BaseModel):
    """生成项目级负样本提示词请求（可选参数）。"""

    provider_id: UUID | None = Field(
        None,
        description="指定 provider，不传则用第一个启用的 provider",
    )


class ProjectNegativePromptGenerateResponse(BaseModel):
    """生成项目级负样本提示词响应。"""

    content: str
    source_count: int = Field(description="用于生成的负样本数量")
