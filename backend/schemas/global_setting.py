"""Pydantic schemas for global settings."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class GlobalPromptResponse(BaseModel):
    """Response payload for the global system prompt endpoint."""

    content: str


class GlobalPromptUpdate(BaseModel):
    """Request payload for updating the global system prompt."""

    content: str = Field(..., max_length=50000)


class NegativePromptResponse(BaseModel):
    """负样本提示词响应。"""

    content: str


class NegativePromptUpdate(BaseModel):
    """Request payload for updating the negative prompt."""

    content: str = Field(..., max_length=50000)


class NegativePromptGenerateRequest(BaseModel):
    """生成负样本提示词请求（可选参数）。"""

    provider_id: UUID | None = Field(
        None,
        description="指定 provider，不传则用第一个启用的 provider",
    )


class NegativePromptGenerateResponse(BaseModel):
    """生成负样本提示词响应。"""

    content: str
    source_count: int = Field(description="用于生成的负样本数量")
