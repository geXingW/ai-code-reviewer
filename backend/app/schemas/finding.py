"""Pydantic schemas for review findings."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas._datetime import AwareDatetime

Severity = Literal["INFO", "WARNING", "BLOCKER"]
FalsePositiveStatus = Literal["NONE", "PENDING", "CONFIRMED", "REJECTED"]
# Finding 生命周期状态：
# - ``open``: 活着的问题；
# - ``resolved``: 判定已修/已合并；``resolved_in_review_id`` 指向那次 review；
# - ``mr_closed``: 所属 MR 关闭（不是合并），问题随 MR 一起"作废"；
#   ``resolved_in_review_id`` 复用来指向 lifecycle 记账 review。
FindingStatus = Literal["open", "resolved", "mr_closed"]


class FindingCreate(BaseModel):
    """Payload for creating a review finding."""

    review_id: UUID
    file_path: str
    line_number: int | None = None
    rule_id: str
    severity: Severity
    title: str
    description: str | None = None
    suggestion: str | None = None
    existing_code: str | None = None
    # LLM 输出的分类字段；未在此约束枚举——渲染层收到无效值会 fallback 到
    # rule_id 推断，避免因为 LLM 偶发"发挥"打断整个 finding 落库。允许值请
    # 参考 app.core.finding_taxonomy.FindingCategory。
    category: str | None = None
    confidence: float = 0.0
    gitlab_discussion_id: str | None = None
    fp_status: FalsePositiveStatus = "NONE"
    fp_marked_by: str | None = None
    fp_marked_at: datetime | None = None
    fp_marked_reason: str | None = None
    fp_reviewed_by: str | None = None
    fp_reviewed_at: datetime | None = None
    fp_review_note: str | None = None


class FindingUpdate(BaseModel):
    """Payload for updating a review finding."""

    review_id: UUID | None = None
    file_path: str | None = None
    line_number: int | None = None
    rule_id: str | None = None
    severity: Severity | None = None
    title: str | None = None
    description: str | None = None
    suggestion: str | None = None
    existing_code: str | None = None
    category: str | None = None
    confidence: float | None = None
    gitlab_discussion_id: str | None = None
    fp_status: FalsePositiveStatus | None = None
    fp_marked_by: str | None = None
    fp_marked_at: datetime | None = None
    fp_marked_reason: str | None = None
    fp_reviewed_by: str | None = None
    fp_reviewed_at: datetime | None = None
    fp_review_note: str | None = None


class FindingRead(BaseModel):
    """Review finding returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_id: UUID
    file_path: str
    line_number: int | None
    rule_id: str
    severity: Severity
    title: str
    description: str | None
    suggestion: str | None
    existing_code: str | None
    # 与 FindingCreate 同源：无效值会在渲染层 fallback 到 rule_id 推断。
    category: str | None = None
    confidence: float
    gitlab_discussion_id: str | None
    fp_status: FalsePositiveStatus
    fp_marked_by: str | None
    fp_marked_at: AwareDatetime | None
    fp_marked_reason: str | None
    fp_reviewed_by: str | None
    fp_reviewed_at: AwareDatetime | None
    fp_review_note: str | None
    # 生命周期状态；DB 侧 String(20) 无 check constraint，Literal 只在 API 边界约束。
    # 老数据 server_default='open'，未来若出现落到未预期取值也会被 Pydantic 校验拦下。
    status: FindingStatus = "open"
    created_at: AwareDatetime
    updated_at: AwareDatetime

    # 展示用冗余字段：由 admin API 层在返回前从 finding.review / finding.review.project
    # 关系里读出填充，不直接映射 ORM 列。方便前端"问题与误报"列表页快速定位到
    # 具体项目 / MR，而不用先点进 review 详情。
    #
    # 注意：``mr_title`` 目前在 Review 表里没有落库列（PR #80 只在 orchestrator
    # 的 ReviewContext 内存里带过），后端 enrich 层暂时始终填 None。字段保留是给
    # 未来把 mr_title 落库后一次性联通用的，前端也应当处理 None 情况。
    project_name: str | None = None
    project_id: UUID | None = None
    mr_iid: str | None = None
    mr_title: str | None = None
    review_created_at: AwareDatetime | None = None
