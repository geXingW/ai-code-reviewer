"""钉钉（DingTalk）自定义机器人 Webhook 推送客户端。

只暴露 MVP 需要的最小接口：发送 markdown 消息。开启「加签」安全设置时，按钉钉
签名算法（``HmacSHA256(timestamp + "\\n" + secret)`` 后 Base64）对请求 URL 追加
``timestamp`` / ``sign`` 查询参数。

参考：https://open.dingtalk.com/document/robots/customize-robot-security-settings
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)


class DingTalkClientError(RuntimeError):
    """钉钉 Webhook 调用失败时抛出（HTTP 非 2xx 或业务 ``errcode != 0``）。"""


class DingTalkClient:
    """钉钉自定义机器人 Webhook 异步客户端。

    Args:
        webhook_url: 钉钉机器人的 Webhook 地址。
        secret: 开启「加签」时的签名密钥；为 ``None`` 表示未启用加签。
        timeout_seconds: 单次请求超时秒数。
    """

    def __init__(
        self,
        webhook_url: str,
        secret: str | None = None,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not webhook_url:
            msg = "DingTalk webhook_url must not be empty."
            raise ValueError(msg)
        self._webhook_url = webhook_url
        self._secret = secret
        self._timeout = timeout_seconds

    def _sign(self, timestamp: int) -> str:
        """按钉钉加签算法生成签名。

        算法：``HmacSHA256(key=secret, message=f"{timestamp}\\n{secret}")`` 后 Base64 编码。
        返回未做 URL 编码的 Base64 字符串，由调用方决定是否 ``quote_plus`` 后拼到 URL。

        Raises:
            ValueError: 未配置 ``secret`` 时调用。
        """

        if not self._secret:
            msg = "DingTalk _sign requires a non-empty secret."
            raise ValueError(msg)
        string_to_sign = f"{timestamp}\n{self._secret}"
        digest = hmac.new(
            self._secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _build_url(self, timestamp: int | None) -> str:
        """构造最终请求 URL；无 ``secret`` 时原样返回。"""

        if not self._secret or timestamp is None:
            return self._webhook_url
        sign = self._sign(timestamp)
        # 钉钉 Webhook 已自带 ``?access_token=xxx``，故追加参数用 ``&``。
        sep = "&" if "?" in self._webhook_url else "?"
        return (
            f"{self._webhook_url}{sep}"
            f"timestamp={timestamp}&sign={quote_plus(sign)}"
        )

    async def send_markdown(
        self,
        title: str,
        text: str,
        at_mobiles: list[str] | None = None,
        at_user_ids: list[str] | None = None,
        is_at_all: bool = False,
    ) -> dict[str, Any]:
        """发送 markdown 类型消息，支持 @ 人。

        Args:
            title: 消息标题（钉钉通知列表展示用）。
            text: markdown 正文。
            at_mobiles: 被 @ 人的手机号列表。钉钉约定：``atMobiles`` 只指定
                @ 范围，**正文中还需出现 ``@手机号`` 文本**才会真正高亮提醒，
                因此这里会把 ``\\n\\n@手机号`` 追加到正文末尾。
            at_user_ids: 被 @ 人的钉钉 userid 列表（同样依赖正文 @ 文本高亮，
                userid 无法拼进正文，一般配合 at_mobiles 使用或仅用于免打扰配置）。
            is_at_all: @ 所有人（``isAtAll=true``）。

        Returns:
            钉钉返回的 JSON 响应。

        Raises:
            DingTalkClientError: HTTP 非 2xx 或响应 ``errcode != 0``。
        """

        if not title.strip():
            msg = "DingTalk message title must not be empty."
            raise ValueError(msg)
        if not text.strip():
            msg = "DingTalk message text must not be empty."
            raise ValueError(msg)
        effective_text = text
        if at_mobiles:
            # 正文末尾追加 @手机号，钉钉 markdown 消息需要正文也有 @ 才高亮。
            at_suffix = "".join(f"@{mobile}" for mobile in at_mobiles)
            effective_text = f"{text}\n\n{at_suffix}"
        # 钉钉加签要求毫秒级时间戳。
        timestamp = int(time.time() * 1000)
        url = self._build_url(timestamp)
        payload: dict[str, Any] = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": effective_text},
        }
        # 未传任何 @ 参数时保持旧请求体（无 ``at`` 字段），兼容既有调用方与测试。
        if at_mobiles or at_user_ids or is_at_all:
            payload["at"] = {
                "atMobiles": list(at_mobiles or []),
                "atUserIds": list(at_user_ids or []),
                "isAtAll": bool(is_at_all),
            }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload)

        if response.status_code < 200 or response.status_code >= 300:
            raise DingTalkClientError(
                f"DingTalk webhook HTTP {response.status_code}: {response.text[:200]}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise DingTalkClientError(
                f"DingTalk webhook returned non-JSON body: {response.text[:200]}",
            ) from exc
        if not isinstance(data, dict):
            return {"data": data}
        errcode = data.get("errcode", 0)
        if errcode not in (0, None):
            raise DingTalkClientError(
                f"DingTalk webhook errcode={errcode}: {data.get('errmsg', '')}",
            )
        return data
