"""Pydantic 序列化辅助：确保所有 datetime 字段带上 UTC 时区。

背景
----
MySQL DATETIME 存储不带 tzinfo，SQLAlchemy 读回的值是 naive；
如果直接被 Pydantic 序列化，emit 出的 ISO 无 ``+00:00``，前端
``new Date()`` 会按浏览器本地时区解析，产生等于本地时区偏移的错位
（UTC+8 环境会显示成 8 小时前）。

用法
----
在需要暴露给前端的 Schema 上，把 ``created_at: datetime`` 改成
``created_at: AwareDatetime``，可空字段用 ``AwareDatetime | None``。
序列化时 naive datetime 会被视作 UTC 强制打上 ``+00:00``。
"""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def ensure_utc(value: datetime | None) -> datetime | None:
    """把 naive datetime 视为 UTC 打上 tzinfo；aware datetime 原样返回。"""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _serialize_aware(value: datetime | None) -> str | None:
    """把 datetime 转为带 tzinfo 的 ISO 字符串；None 透传。"""

    aware = ensure_utc(value)
    return aware.isoformat() if aware is not None else None


# 通用 Annotated 类型：暴露给前端的 datetime 字段统一使用。
# ``when_used="json"`` 保证 ``.model_dump()``（python 模式）依然返回
# datetime 对象，仅 JSON 序列化时输出带时区的 ISO 字符串。
AwareDatetime = Annotated[
    datetime,
    PlainSerializer(_serialize_aware, return_type=str, when_used="json"),
]
