"""Pydantic schemas for per-project notification channels."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from schemas._datetime import AwareDatetime

NotificationChannelType = Literal["dingtalk", "feishu"]


class ProjectNotificationChannelBase(BaseModel):
    """共享字段：渠道类型、名称、Webhook、签名密钥、启用状态。"""

    channel_type: NotificationChannelType
    name: str
    webhook_url: str
    secret: str | None = None
    enabled: bool = True


class ProjectNotificationChannelCreate(ProjectNotificationChannelBase):
    """创建通知渠道的请求体，``project_id`` 取自 URL 路径。"""

    project_id: UUID | None = None


class ProjectNotificationChannelUpdate(BaseModel):
    """更新通知渠道的请求体，所有字段可选（PATCH 语义）。"""

    channel_type: NotificationChannelType | None = None
    name: str | None = None
    webhook_url: str | None = None
    secret: str | None = None
    enabled: bool | None = None


class ProjectNotificationChannelRead(BaseModel):
    """通知渠道响应体：敏感字段（webhook_url / secret）脱敏返回 ``****``。

    与 ``ProjectRead`` 的 ``gitlab_access_token`` / ``webhook_secret`` 一致，
    ``EncryptedString`` 读出时已自动解密，这里在序列化前统一替换为掩码，
    避免把 Webhook 地址与签名密钥回吐给前端。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    channel_type: NotificationChannelType
    name: str
    webhook_url: str
    secret: str | None
    enabled: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("webhook_url", mode="before")
    @classmethod
    def mask_webhook_url(cls, value: object) -> str:
        """Mask the webhook URL in API responses."""

        return "****"

    @field_validator("secret", mode="before")
    @classmethod
    def mask_secret(cls, value: object) -> str | None:
        """Mask the signing secret; preserve ``None`` when unset."""

        return "****" if value is not None else None
