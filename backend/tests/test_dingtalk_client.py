"""Tests for the DingTalk webhook client (signing + send_markdown dispatch)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
import respx
from httpx import Response

from integrations.dingtalk.client import DingTalkClient, DingTalkClientError

_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=abc"
# respx 路由只用 host+path 匹配，避免客户端追加 ``&timestamp`` / ``&sign`` 时与
# 带 query 的 pattern 产生严格匹配问题。
_ROUTE = "https://oapi.dingtalk.com/robot/send"
_SECRET = "SECtest123"


def _expected_sign(secret: str, timestamp: int) -> str:
    """Independently recompute the DingTalk sign to assert against the client."""

    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def test_sign_matches_dingtalk_algorithm() -> None:
    """``_sign`` produces ``Base64(HmacSHA256(timestamp + '\\n' + secret))``."""

    client = DingTalkClient(webhook_url=_WEBHOOK, secret=_SECRET)
    sign = client._sign(1_700_000_000_000)

    assert sign == _expected_sign(_SECRET, 1_700_000_000_000)


def test_sign_without_secret_raises() -> None:
    """未配置 secret 时调用 ``_sign`` 应显式报错而非静默返回空签名。"""

    client = DingTalkClient(webhook_url=_WEBHOOK)
    with pytest.raises(ValueError):
        client._sign(1_700_000_000_000)


@pytest.mark.asyncio
@respx.mock
async def test_send_markdown_without_secret_posts_markdown_payload() -> None:
    """无 secret 时只发送 markdown 请求体，URL 不追加签名参数。"""

    route = respx.post(_ROUTE).mock(
        return_value=Response(200, json={"errcode": 0, "errmsg": "ok"}),
    )
    client = DingTalkClient(webhook_url=_WEBHOOK)
    data = await client.send_markdown("审查完成", "## hello")

    assert route.called
    assert data["errcode"] == 0
    payload = json.loads(route.calls.last.request.content)
    assert payload == {
        "msgtype": "markdown",
        "markdown": {"title": "审查完成", "text": "## hello"},
    }
    assert "timestamp" not in route.calls.last.request.url.params
    assert "sign" not in route.calls.last.request.url.params


@pytest.mark.asyncio
@respx.mock
async def test_send_markdown_with_secret_appends_timestamp_and_sign() -> None:
    """开启加签时 URL 追加 ``timestamp`` / ``sign``，且 sign 与算法一致。"""

    route = respx.post(_ROUTE).mock(
        return_value=Response(200, json={"errcode": 0, "errmsg": "ok"}),
    )
    client = DingTalkClient(webhook_url=_WEBHOOK, secret=_SECRET)
    await client.send_markdown("审查完成", "## hello")

    assert route.called
    params = route.calls.last.request.url.params
    timestamp = int(params["timestamp"])
    # URL 上的 sign（经 quote_plus -> respx 已解码）应与对同一 timestamp 重算的签名一致。
    assert params["sign"] == client._sign(timestamp)


@pytest.mark.asyncio
@respx.mock
async def test_send_markdown_raises_on_errcode() -> None:
    """钉钉返回 ``errcode != 0`` 时抛 :class:`DingTalkClientError`。"""

    respx.post(_ROUTE).mock(
        return_value=Response(200, json={"errcode": 310000, "errmsg": "ip not in whitelist"}),
    )
    client = DingTalkClient(webhook_url=_WEBHOOK)
    with pytest.raises(DingTalkClientError):
        await client.send_markdown("审查完成", "## hello")


@pytest.mark.asyncio
@respx.mock
async def test_send_markdown_raises_on_http_error() -> None:
    """HTTP 非 2xx 时抛 :class:`DingTalkClientError`。"""

    respx.post(_ROUTE).mock(return_value=Response(500, text="boom"))
    client = DingTalkClient(webhook_url=_WEBHOOK)
    with pytest.raises(DingTalkClientError):
        await client.send_markdown("审查完成", "## hello")


@pytest.mark.asyncio
@respx.mock
async def test_send_markdown_raises_on_non_json_body() -> None:
    """2xx 但返回非 JSON 时抛 :class:`DingTalkClientError`。"""

    respx.post(_ROUTE).mock(return_value=Response(200, text="<html>not json</html>"))
    client = DingTalkClient(webhook_url=_WEBHOOK)
    with pytest.raises(DingTalkClientError):
        await client.send_markdown("审查完成", "## hello")
