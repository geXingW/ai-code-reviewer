"""Tests for NotificationService: message building, dispatch, and fail-silent behavior.

These are pure unit tests: ``_resolve_channels`` is exercised via monkeypatched
repositories (no database), and the DingTalk client is replaced by an injected
fake factory so no HTTP is performed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from repositories.project import ProjectRepository
from repositories.project_notification_channel import (
    ProjectNotificationChannelRepository,
)
from services.notification_service import NotificationService

_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=abc"


def _channel(
    *,
    channel_type: str = "dingtalk",
    webhook_url: str = _WEBHOOK,
    secret: str | None = None,
    enabled: bool = True,
    channel_id: str = "ch-1",
) -> SimpleNamespace:
    """Build a lightweight stand-in for a ProjectNotificationChannel row."""

    return SimpleNamespace(
        id=channel_id,
        project_id="proj-1",
        channel_type=channel_type,
        webhook_url=webhook_url,
        secret=secret,
        enabled=enabled,
    )


class _FakeSessionFactory:
    """Yields a fixed fake session, mimicking ``async_sessionmaker()``."""

    def __init__(self, session: object) -> None:
        self._session = session

    def __call__(self) -> _FakeSessionFactory:
        return self

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *exc: object) -> None:
        return None


# --------------------------------------------------------------------------- #
# _build_review_message
# --------------------------------------------------------------------------- #


def test_build_message_done_with_blocker() -> None:
    """有阻断的完成消息：标题带「存在阻断」，正文含 finding / blocker 计数与详情链接。"""

    svc = NotificationService(session_factory=None)
    title, text = svc._build_review_message(
        {
            "review_id": "r-1",
            "mr_iid": 42,
            "mr_title": "feat: add login",
            "finding_count": 3,
            "has_blocker": True,
            "blocker_count": 2,
            "detail_url": "http://x/reviews/r-1",
            "status": "done",
        },
    )
    assert "MR !42" in title
    assert "存在阻断" in title
    assert "3" in text and "2" in text
    assert "http://x/reviews/r-1" in text
    assert "feat: add login" in text


def test_build_message_done_no_blocker() -> None:
    """无阻断的完成消息：标题带「无阻断」。"""

    svc = NotificationService(session_factory=None)
    title, text = svc._build_review_message(
        {
            "review_id": "r-1",
            "mr_iid": 42,
            "finding_count": 0,
            "has_blocker": False,
            "blocker_count": 0,
            "status": "done",
        },
    )
    assert "无阻断" in title
    assert "0" in text


def test_build_message_engine_error() -> None:
    """引擎异常消息：标题带「审查异常」，结果行为引擎失败。"""

    svc = NotificationService(session_factory=None)
    title, text = svc._build_review_message(
        {"review_id": "r-1", "mr_iid": 7, "status": "engine_error"},
    )
    assert "审查异常" in title
    assert "MR !7" in title
    assert "引擎执行失败" in text


# --------------------------------------------------------------------------- #
# send_review_completed dispatch (fail-silent)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_pushes_to_all_enabled_dingtalk_channels() -> None:
    """两个启用的钉钉渠道都应被推送，client 用解密后的 webhook_url / secret 构造。"""

    fake_client = AsyncMock()
    fake_client.send_markdown = AsyncMock(return_value={"errcode": 0})
    client_factory = MagicMock(return_value=fake_client)
    svc = NotificationService(
        session_factory=MagicMock(),
        client_factory=client_factory,
    )
    svc._resolve_channels = AsyncMock(
        return_value=[
            _channel(secret="s1", channel_id="c1"),
            _channel(secret=None, channel_id="c2"),
        ],
    )

    await svc.send_review_completed(
        gitlab_project_id=999,
        review_data={"review_id": "r-1", "mr_iid": 1, "status": "done"},
    )

    assert client_factory.call_count == 2
    client_factory.assert_any_call(webhook_url=_WEBHOOK, secret="s1")
    client_factory.assert_any_call(webhook_url=_WEBHOOK, secret=None)
    assert fake_client.send_markdown.await_count == 2


@pytest.mark.asyncio
async def test_send_skips_unsupported_channel_type() -> None:
    """非钉钉渠道（如 feishu）应跳过，不调用客户端。"""

    fake_client = AsyncMock()
    client_factory = MagicMock(return_value=fake_client)
    svc = NotificationService(
        session_factory=MagicMock(),
        client_factory=client_factory,
    )
    svc._resolve_channels = AsyncMock(return_value=[_channel(channel_type="feishu")])

    await svc.send_review_completed(gitlab_project_id=999, review_data={"status": "done"})

    fake_client.send_markdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_fail_silent_when_no_channels() -> None:
    """无启用渠道时不推送、不抛异常。"""

    fake_client = AsyncMock()
    svc = NotificationService(
        session_factory=MagicMock(),
        client_factory=MagicMock(return_value=fake_client),
    )
    svc._resolve_channels = AsyncMock(return_value=[])

    await svc.send_review_completed(gitlab_project_id=999, review_data={"status": "done"})

    fake_client.send_markdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_fail_silent_on_push_error_continues_other_channels() -> None:
    """单个渠道推送抛错应被吞掉，不影响其它渠道，且整体不抛异常。"""

    ok_client = AsyncMock()
    ok_client.send_markdown = AsyncMock(return_value={"errcode": 0})

    def factory(webhook_url: str, secret: str | None) -> AsyncMock:
        if "fail" in webhook_url:
            bad = AsyncMock()
            bad.send_markdown = AsyncMock(side_effect=RuntimeError("boom"))
            return bad
        return ok_client

    svc = NotificationService(session_factory=MagicMock(), client_factory=factory)
    svc._resolve_channels = AsyncMock(
        return_value=[
            _channel(webhook_url="https://fail", channel_id="c1"),
            _channel(webhook_url="https://ok", channel_id="c2"),
        ],
    )

    # 不应抛异常
    await svc.send_review_completed(gitlab_project_id=999, review_data={"status": "done"})

    # 失败渠道不应阻断第二个渠道的推送
    ok_client.send_markdown.assert_awaited_once()


# --------------------------------------------------------------------------- #
# _resolve_channels (real implementation via monkeypatched repositories)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resolve_channels_returns_empty_when_session_factory_none() -> None:
    """未注入 session_factory 时返回空（MVP 兼容）。"""

    svc = NotificationService(session_factory=None)
    assert await svc._resolve_channels(999) == []


@pytest.mark.asyncio
async def test_resolve_channels_returns_enabled_channels_for_registered_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project 已注册时返回该项目的启用渠道。"""

    fake_session = MagicMock()
    sf = _FakeSessionFactory(fake_session)
    fake_project = SimpleNamespace(id="proj-uuid")
    channels = [_channel()]
    monkeypatch.setattr(
        ProjectRepository,
        "get_by_gitlab_project_id",
        AsyncMock(return_value=fake_project),
    )
    monkeypatch.setattr(
        ProjectNotificationChannelRepository,
        "get_enabled_by_project",
        AsyncMock(return_value=channels),
    )
    svc = NotificationService(session_factory=sf)

    result = await svc._resolve_channels(123)

    assert result == channels


@pytest.mark.asyncio
async def test_resolve_channels_returns_empty_when_project_not_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project 未注册时返回空，且不应查询渠道。"""

    fake_session = MagicMock()
    sf = _FakeSessionFactory(fake_session)
    monkeypatch.setattr(
        ProjectRepository,
        "get_by_gitlab_project_id",
        AsyncMock(return_value=None),
    )
    channel_spy = AsyncMock(return_value=[])
    monkeypatch.setattr(
        ProjectNotificationChannelRepository,
        "get_enabled_by_project",
        channel_spy,
    )
    svc = NotificationService(session_factory=sf)

    result = await svc._resolve_channels(123)

    assert result == []
    channel_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_channels_fail_silent_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """渠道查询底层异常时返回空，不向调用方抛出。"""

    fake_session = MagicMock()
    sf = _FakeSessionFactory(fake_session)
    monkeypatch.setattr(
        ProjectRepository,
        "get_by_gitlab_project_id",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    svc = NotificationService(session_factory=sf)

    result = await svc._resolve_channels(123)

    assert result == []
