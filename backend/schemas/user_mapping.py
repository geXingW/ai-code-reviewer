"""Pydantic schemas for per-project GitLab↔DingTalk user mappings."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from schemas._datetime import AwareDatetime


class UserMappingCreate(BaseModel):
    """创建用户映射的请求体，``project_id`` 取自 URL 路径。"""

    project_id: UUID | None = None
    gitlab_username: str = Field(min_length=1, max_length=255)
    dingtalk_mobile: str = Field(min_length=1, max_length=32)
    dingtalk_userid: str | None = Field(default=None, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)


class UserMappingUpdate(BaseModel):
    """更新用户映射的请求体，所有字段可选（PUT 语义，未传字段保持原值）。"""

    gitlab_username: str | None = Field(default=None, min_length=1, max_length=255)
    dingtalk_mobile: str | None = Field(default=None, min_length=1, max_length=32)
    dingtalk_userid: str | None = Field(default=None, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)


class UserMappingResponse(BaseModel):
    """用户映射响应体（无敏感字段，手机号本身即 @ 人所需，明文返回）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    gitlab_username: str
    dingtalk_mobile: str
    dingtalk_userid: str | None
    display_name: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
