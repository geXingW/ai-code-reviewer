"""Pydantic schemas for shared review rules."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas._datetime import AwareDatetime

Severity = Literal["INFO", "WARNING", "BLOCKER"]


class RuleCreate(BaseModel):
    """Payload for creating a shared review rule."""

    # rule_id 可选：留空时由后端从 title 自动生成 slug（见 admin._generate_rule_slug）。
    rule_id: str | None = None
    title: str
    prompt_snippet: str
    severity_default: Severity = "WARNING"
    # 规则的默认分类；LLM 以此为参考写入 finding.category。允许值参考
    # app.core.finding_taxonomy.FindingCategory；schema 层留 str 以便
    # seed / API 侧扩展新枚举时不用同步改一遍 Literal。
    category_default: str | None = None
    languages: list[Any] = Field(default_factory=list)
    path_patterns: list[Any] = Field(default_factory=list)
    enabled: bool = True
    grace_period_until: datetime | None = None


class RuleUpdate(BaseModel):
    """Payload for updating a shared review rule."""

    rule_id: str | None = None
    title: str | None = None
    prompt_snippet: str | None = None
    severity_default: Severity | None = None
    category_default: str | None = None
    languages: list[Any] | None = None
    path_patterns: list[Any] | None = None
    enabled: bool | None = None
    grace_period_until: datetime | None = None


class RuleRead(BaseModel):
    """Shared review rule returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_id: str
    title: str
    prompt_snippet: str
    severity_default: Severity
    category_default: str | None
    languages: list[Any]
    path_patterns: list[Any]
    enabled: bool
    grace_period_until: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
