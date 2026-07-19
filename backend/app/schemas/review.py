"""Pydantic schemas for AI review records."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas._datetime import AwareDatetime

# PR #86 起 orchestrator 会把 AI 引擎挂了显式落一条 status='engine_error' 的评审
# 记录（用于运营统计与前端红/橙徽章）。schema 层 Literal 必须同步包含此值，
# 否则 GET /api/reviews 从 DB 读到 engine_error 行会因 ValidationError 500。
ReviewStatus = Literal["pending", "running", "done", "failed", "engine_error"]

# PR #89 增量审查串链的模式：
# - ``full``：全量审查（默认，也是老数据 server_default）
# - ``incremental``：仅审查相较上一次 push 的增量 diff
# - ``reuse``：同一 commit 之前审过、直接复用（orchestrator 逻辑不新建 Review 行，
#   因此实际上 DB 中不会出现 ``reuse``；此 Literal 是给"reuse 结果重推 note"链路
#   在展示层用的对齐值，schema 层允许 API 回显时给出 reuse 语义。）
ReviewMode = Literal["full", "incremental", "reuse"]

# PR #96：MR 生命周期事件（close / merge webhook）触发的"记账 Review"专用标签。
# - ``mr_closed`` → MR 关闭事件；相关 finding 被标记为 mr_closed
# - ``mr_merged`` → MR 合并事件；相关 finding 被标记为 resolved
# 普通审查该字段为 None（走 review_mode 徽章）；前端有值时优先展示专属徽章。
ReviewLifecycleEvent = Literal["mr_closed", "mr_merged"]


class ReviewCreate(BaseModel):
    """Payload for creating a review record."""

    project_id: UUID
    mr_iid: str
    source_branch: str
    target_branch: str
    commit_sha: str
    status: ReviewStatus = "pending"
    engine_used: str | None = None
    provider_used: str | None = None
    policy_applied: UUID | None = None
    has_blocker: bool = False
    finding_count: int = 0
    duration_ms: int | None = None
    raw_llm_output: str | None = None
    # PR #89：增量审查串链字段。Create 时默认 full；base_sha / parent_review_id
    # 在增量场景由 orchestrator 显式传入。
    base_sha: str | None = None
    parent_review_id: UUID | None = None
    review_mode: ReviewMode = "full"


class ReviewUpdate(BaseModel):
    """Payload for updating a review record."""

    project_id: UUID | None = None
    mr_iid: str | None = None
    source_branch: str | None = None
    target_branch: str | None = None
    commit_sha: str | None = None
    status: ReviewStatus | None = None
    engine_used: str | None = None
    provider_used: str | None = None
    policy_applied: UUID | None = None
    has_blocker: bool | None = None
    finding_count: int | None = None
    duration_ms: int | None = None
    raw_llm_output: str | None = None
    # PR #89：增量审查串链字段。Update 全部 optional，仅在需要修正串链时使用。
    base_sha: str | None = None
    parent_review_id: UUID | None = None
    review_mode: ReviewMode | None = None


class ReviewRead(BaseModel):
    """Review record returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    mr_iid: str
    source_branch: str
    target_branch: str
    commit_sha: str
    status: ReviewStatus
    engine_used: str | None
    provider_used: str | None
    policy_applied: UUID | None
    has_blocker: bool
    finding_count: int
    duration_ms: int | None
    raw_llm_output: str | None
    # 展示用冗余字段：project_name 来自关联 Project.name，rules_used 为 findings 的
    # rule_id 去重列表。由 admin API 层在返回前填充，不直接映射 ORM 列。
    project_name: str | None = None
    rules_used: list[str] = Field(default_factory=list)
    # PR #89：增量审查串链字段透传给前端，让"全量 / 增量 / 复用"一眼可辨。
    # base_sha 与 parent_review_id 老数据可能为 NULL；review_mode 数据库层
    # server_default='full'，schema 层同样非空（老数据取默认值 'full'）。
    base_sha: str | None = None
    parent_review_id: UUID | None = None
    review_mode: ReviewMode = "full"
    # PR #96：MR 生命周期事件记账 Review 的标签。普通审查为 None。
    lifecycle_event: ReviewLifecycleEvent | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
