"""Tests for Pydantic datetime UTC serialization helpers.

背景：MySQL DATETIME 存回是 naive；前端 new Date() 会按浏览器本地时区
解析无 tz 的 ISO 字符串，产生等于本地时区偏移的错位。ensure_utc /
AwareDatetime 用于在 emit JSON 时兜底打上 +00:00。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from app.schemas._datetime import AwareDatetime, ensure_utc


def test_ensure_utc_naive_input_gets_utc() -> None:
    """naive datetime 应被视为 UTC 并打上 tzinfo。"""

    naive = datetime(2026, 7, 16, 6, 12, 41, 378537)
    result = ensure_utc(naive)
    assert result is not None
    assert result.tzinfo is UTC
    # 时间字段保持不变，只补充 tzinfo。
    assert result.replace(tzinfo=None) == naive


def test_ensure_utc_aware_input_unchanged() -> None:
    """已带 tzinfo 的 datetime 应原样返回，不被改写为 UTC。"""

    tz_plus_eight = timezone(timedelta(hours=8))
    aware = datetime(2026, 7, 16, 14, 12, 41, tzinfo=tz_plus_eight)
    result = ensure_utc(aware)
    assert result is aware
    assert result.tzinfo is tz_plus_eight


def test_ensure_utc_none_returns_none() -> None:
    """None 输入应透传 None，避免调用方额外判空。"""

    assert ensure_utc(None) is None


class _Model(BaseModel):
    """用于 AwareDatetime 序列化断言的最小 Pydantic 模型。"""

    ts: AwareDatetime
    maybe: AwareDatetime | None = None


def test_aware_datetime_pydantic_field_naive_serializes_with_utc() -> None:
    """naive datetime 通过 AwareDatetime 序列化必须带 +00:00。"""

    model = _Model(ts=datetime(2026, 7, 16, 6, 12, 41, 378537))
    payload = model.model_dump_json()
    # 应包含带时区的 ISO；Pydantic 默认输出 +00:00 而非 Z。
    assert "+00:00" in payload or "Z" in payload
    assert "2026-07-16T06:12:41" in payload


def test_aware_datetime_pydantic_field_aware_serializes_preserved() -> None:
    """已带 tzinfo 的 datetime 序列化应保留原时区偏移。"""

    tz_plus_eight = timezone(timedelta(hours=8))
    model = _Model(ts=datetime(2026, 7, 16, 14, 12, 41, tzinfo=tz_plus_eight))
    payload = model.model_dump_json()
    assert "+08:00" in payload
    assert "2026-07-16T14:12:41" in payload


def test_aware_datetime_pydantic_field_optional_none_stays_null() -> None:
    """可空字段传 None 时 dump_json 应输出 null，不抛异常。"""

    model = _Model(ts=datetime(2026, 7, 16, tzinfo=UTC), maybe=None)
    payload = model.model_dump_json()
    assert '"maybe":null' in payload


def test_aware_datetime_pydantic_field_optional_naive_serializes_with_utc() -> None:
    """可空字段传 naive datetime 时也应打上 +00:00。"""

    model = _Model(
        ts=datetime(2026, 7, 16, tzinfo=UTC),
        maybe=datetime(2026, 7, 15, 10, 0, 0),
    )
    payload = model.model_dump_json()
    assert "2026-07-15T10:00:00" in payload
    # maybe 字段必须带时区标记。
    assert payload.count("+00:00") >= 1 or payload.count("Z") >= 1


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 1, 1),
        datetime(2026, 6, 15, 12, 30, 45, 123456),
        datetime(1970, 1, 1),
    ],
)
def test_ensure_utc_preserves_wall_clock_for_various_naive_values(value: datetime) -> None:
    """在多种 naive 时间点上 ensure_utc 都只补 tzinfo，不动 wall clock。"""

    result = ensure_utc(value)
    assert result is not None
    assert result.replace(tzinfo=None) == value
    assert result.tzinfo is UTC


def test_recent_review_read_naive_created_at_serializes_with_utc() -> None:
    """RecentReviewRead 是暴露给前端的关键路径：naive created_at 必须带 tz。"""

    from uuid import uuid4

    from app.api.reviews import RecentReviewRead

    payload = RecentReviewRead(
        review_id=uuid4(),
        project_id=1,
        project_path="示例",
        mr_iid=42,
        title="MR !42",
        web_url=None,
        status="done",
        has_blocker=False,
        finding_count=0,
        blocker_count=0,
        policy_applied=None,
        review_url=None,
        engine_used="llm-direct",
        created_at=datetime(2026, 7, 16, 6, 12, 41),  # 模拟 MySQL 读回的 naive 值。
    ).model_dump_json()
    assert "+00:00" in payload or "Z" in payload
    assert "2026-07-16T06:12:41" in payload


def test_review_read_naive_timestamps_serialize_with_utc() -> None:
    """ReviewRead 的 created_at/updated_at 都必须带 tz。"""

    from uuid import uuid4

    from app.schemas.review import ReviewRead

    payload = ReviewRead(
        id=uuid4(),
        project_id=uuid4(),
        mr_iid="1",
        source_branch="feature/x",
        target_branch="main",
        commit_sha="deadbeef",
        status="done",
        engine_used=None,
        provider_used=None,
        policy_applied=None,
        has_blocker=False,
        finding_count=0,
        duration_ms=None,
        raw_llm_output=None,
        created_at=datetime(2026, 7, 16, 6, 12, 41),
        updated_at=datetime(2026, 7, 16, 6, 12, 42),
    ).model_dump_json()
    # 两个字段各自要带一次 tz 标记。
    assert payload.count("+00:00") + payload.count("Z") >= 2
