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
from repositories.user_mapping_repository import UserMappingRepository
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


def _summary_item(title: str, file_path: str, line_number: int | None) -> dict[str, object]:
    """构造 findings_summary 里的单条 item。"""

    return {"title": title, "file_path": file_path, "line_number": line_number}


def test_build_message_contains_mr_link_and_grouped_findings() -> None:
    """新模板：正文含 MR 链接、按级别分组的 finding 列表与正确计数。"""

    svc = NotificationService(session_factory=None)
    title, text = svc._build_review_message(
        {
            "review_id": "r-1",
            "mr_iid": 42,
            "mr_title": "修复用户登录bug",
            "finding_count": 4,
            "has_blocker": True,
            "blocker_count": 2,
            "detail_url": "http://x/reviews/r-1",
            "status": "done",
            "mr_web_url": "https://gitlab.example.com/group/project/-/merge_requests/42",
            "mr_author_username": "alice",
            "findings_summary": [
                {
                    "severity": "BLOCKER",
                    "items": [
                        _summary_item("SQL 注入风险", "auth/login.py", 45),
                        _summary_item("硬编码密钥", "config/database.py", 12),
                    ],
                },
                {
                    "severity": "WARNING",
                    "items": [_summary_item("未处理异常", "services/user.py", 88)],
                },
            ],
        },
    )
    assert "存在阻断" in title
    # MR 链接
    assert (
        "[点击查看](https://gitlab.example.com/group/project/-/merge_requests/42)" in text
    )
    # 结果行按级别计数
    assert "🔴 阻断 2 个" in text
    assert "🟡 警告 1 个" in text
    assert "🔵 提示 0 个" in text
    # 分组列表：标题 + 带行号的条目
    assert "🔴 阻断问题 (2)" in text
    assert "🟡 警告问题 (1)" in text
    assert "**SQL 注入风险** - `auth/login.py:45`" in text
    # 详情链接仍在
    assert "http://x/reviews/r-1" in text


def test_build_message_truncates_warning_and_info_to_five_items() -> None:
    """WARNING / INFO 各最多展示 5 条，超出部分提示「还有 N 条」。"""

    svc = NotificationService(session_factory=None)
    warnings = [
        _summary_item(f"警告{i}", f"w{i}.py", i) for i in range(1, 9)
    ]
    infos = [_summary_item(f"提示{i}", f"i{i}.py", i) for i in range(1, 7)]
    _, text = svc._build_review_message(
        {
            "review_id": "r-1",
            "mr_iid": 1,
            "finding_count": 14,
            "has_blocker": False,
            "status": "done",
            "findings_summary": [
                {"severity": "WARNING", "items": warnings},
                {"severity": "INFO", "items": infos},
            ],
        },
    )
    assert "警告1" in text and "警告5" in text
    assert "警告6" not in text
    assert "还有 3 条" in text  # WARNING 8 - 5
    assert "提示1" in text and "提示5" in text
    assert "提示6" not in text
    assert "还有 1 条" in text  # INFO 6 - 5


def test_build_message_shows_all_blockers() -> None:
    """BLOCKER 全量展示，不做截断。"""

    svc = NotificationService(session_factory=None)
    blockers = [_summary_item(f"阻断{i}", f"b{i}.py", i) for i in range(1, 8)]
    _, text = svc._build_review_message(
        {
            "review_id": "r-1",
            "mr_iid": 1,
            "finding_count": 7,
            "has_blocker": True,
            "blocker_count": 7,
            "status": "done",
            "findings_summary": [{"severity": "BLOCKER", "items": blockers}],
        },
    )
    assert "阻断问题 (7)" in text
    for i in range(1, 8):
        assert f"**阻断{i}**" in text


def test_build_message_without_findings_omits_list_section() -> None:
    """finding 为空时不渲染问题列表部分，退回旧的总量结果行。"""

    svc = NotificationService(session_factory=None)
    _, text = svc._build_review_message(
        {
            "review_id": "r-1",
            "mr_iid": 1,
            "finding_count": 0,
            "has_blocker": False,
            "status": "done",
            "findings_summary": [],
        },
    )
    assert "问题 (" not in text
    assert "发现问题 0 个" in text


def test_build_message_truncates_overlong_text() -> None:
    """接近 20000 字的超长消息被安全截断，不抛异常。"""

    svc = NotificationService(session_factory=None)
    huge_title = "超" * 300
    blockers = [
        {"severity": "BLOCKER", "items": [_summary_item(huge_title, "b.py", 1)]}
    ] * 100
    _, text = svc._build_review_message(
        {
            "review_id": "r-1",
            "mr_iid": 1,
            "finding_count": 100,
            "has_blocker": True,
            "blocker_count": 100,
            "status": "done",
            "findings_summary": blockers,
        },
    )
    assert len(text) < 20000
    assert "消息过长已截断" in text


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
# @ 创建人（user_mappings 映射解析）
# --------------------------------------------------------------------------- #


def _fake_mapping(mobile: str) -> SimpleNamespace:
    """Build a lightweight stand-in for a UserMapping row."""

    return SimpleNamespace(
        id="um-1",
        project_id="proj-uuid",
        gitlab_username="alice",
        dingtalk_mobile=mobile,
        dingtalk_userid=None,
        display_name="Alice",
    )


@pytest.mark.asyncio
async def test_send_ats_mr_author_when_mapping_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """映射命中时 send_markdown 应带 ``at_mobiles=["手机号"]``。"""

    fake_client = AsyncMock()
    fake_client.send_markdown = AsyncMock(return_value={"errcode": 0})
    svc = NotificationService(
        session_factory=MagicMock(),
        client_factory=MagicMock(return_value=fake_client),
    )
    svc._resolve_channels = AsyncMock(return_value=[_channel()])
    monkeypatch.setattr(
        ProjectRepository,
        "get_by_gitlab_project_id",
        AsyncMock(return_value=SimpleNamespace(id="proj-uuid")),
    )
    monkeypatch.setattr(
        UserMappingRepository,
        "get_by_gitlab_username",
        AsyncMock(return_value=_fake_mapping("13800138000")),
    )

    await svc.send_review_completed(
        gitlab_project_id=123,
        review_data={
            "review_id": "r-1",
            "mr_iid": 42,
            "status": "done",
            "mr_author_username": "alice",
        },
    )

    fake_client.send_markdown.assert_awaited_once()
    kwargs = fake_client.send_markdown.await_args.kwargs
    assert kwargs["at_mobiles"] == ["13800138000"]


@pytest.mark.asyncio
async def test_send_without_mapping_pushes_without_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """映射表查不到用户名（含映射表为空）时正常推送，不 @ 人、不报错。"""

    fake_client = AsyncMock()
    fake_client.send_markdown = AsyncMock(return_value={"errcode": 0})
    svc = NotificationService(
        session_factory=MagicMock(),
        client_factory=MagicMock(return_value=fake_client),
    )
    svc._resolve_channels = AsyncMock(return_value=[_channel()])
    monkeypatch.setattr(
        ProjectRepository,
        "get_by_gitlab_project_id",
        AsyncMock(return_value=SimpleNamespace(id="proj-uuid")),
    )
    monkeypatch.setattr(
        UserMappingRepository,
        "get_by_gitlab_username",
        AsyncMock(return_value=None),
    )

    await svc.send_review_completed(
        gitlab_project_id=123,
        review_data={
            "review_id": "r-1",
            "mr_iid": 42,
            "status": "done",
            "mr_author_username": "bob",
        },
    )

    fake_client.send_markdown.assert_awaited_once()
    assert fake_client.send_markdown.await_args.kwargs["at_mobiles"] is None


@pytest.mark.asyncio
async def test_send_without_mr_author_skips_mapping_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MR 无作者信息时不查映射表，照常推送、不 @ 人。"""

    fake_client = AsyncMock()
    fake_client.send_markdown = AsyncMock(return_value={"errcode": 0})
    svc = NotificationService(
        session_factory=MagicMock(),
        client_factory=MagicMock(return_value=fake_client),
    )
    svc._resolve_channels = AsyncMock(return_value=[_channel()])
    mapping_spy = AsyncMock(return_value=_fake_mapping("13800138000"))
    monkeypatch.setattr(UserMappingRepository, "get_by_gitlab_username", mapping_spy)

    await svc.send_review_completed(
        gitlab_project_id=123,
        review_data={"review_id": "r-1", "mr_iid": 42, "status": "done"},
    )

    mapping_spy.assert_not_awaited()
    fake_client.send_markdown.assert_awaited_once()
    assert fake_client.send_markdown.await_args.kwargs["at_mobiles"] is None


@pytest.mark.asyncio
async def test_resolve_at_mobiles_fail_silent_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """映射查询抛异常时返回空列表，不影响推送主流程。"""

    svc = NotificationService(session_factory=MagicMock())
    monkeypatch.setattr(
        ProjectRepository,
        "get_by_gitlab_project_id",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    result = await svc._resolve_at_mobiles(
        123,
        {"mr_author_username": "alice"},
    )

    assert result == []


@pytest.mark.asyncio
async def test_resolve_at_mobiles_empty_when_session_factory_none() -> None:
    """未注入 session_factory 时返回空（MVP 兼容）。"""

    svc = NotificationService(session_factory=None)
    assert await svc._resolve_at_mobiles(123, {"mr_author_username": "alice"}) == []


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
