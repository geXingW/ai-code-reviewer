"""Pydantic schemas for global settings."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GlobalPromptResponse(BaseModel):
    """Response payload for the global system prompt endpoint."""

    content: str


class GlobalPromptUpdate(BaseModel):
    """Request payload for updating the global system prompt."""

    content: str = Field(..., max_length=50000)
