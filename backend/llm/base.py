"""LLM provider contracts, common models, and concrete HTTP adapters."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal, Protocol, cast
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

ProviderProtocol = Literal["openai_compatible", "anthropic", "custom"]
# "tool" 角色用于 function-calling 循环：把工具执行结果回填给模型。
ChatRole = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    """One function call requested by the model."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    # JSON-encoded arguments string；部分 provider 直接返回 dict，归一化时统一序列化。
    arguments: str = "{}"


class ToolSpec(BaseModel):
    """Function definition advertised to the model via native tool calling."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    # JSON Schema object describing the function parameters.
    parameters: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    """Provider-neutral chat message."""

    model_config = ConfigDict(extra="forbid")

    role: ChatRole
    content: str = ""
    # Assistant message: tool calls the model requested（OpenAI / Anthropic 均支持）。
    tool_calls: list[ToolCall] | None = None
    # Tool message: which call this content answers（OpenAI 语义，Anthropic 归一化时转换）。
    tool_call_id: str | None = None


class ChatResponse(BaseModel):
    """Provider-neutral chat response."""

    model_config = ConfigDict(extra="forbid")

    content: str
    model: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class LLMProviderConfig(BaseModel):
    """Resolved provider settings used to instantiate an LLM adapter."""

    model_config = ConfigDict(extra="forbid")

    provider_id: UUID
    protocol: ProviderProtocol
    base_url: str
    api_key: str = Field(repr=False)
    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    default_json_mode: bool = True
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class LLMError(Exception):
    """Base exception for all normalized LLM provider failures."""


class RateLimitError(LLMError):
    """Raised when upstream returns a rate-limit response."""


class AuthError(LLMError):
    """Raised when provider credentials are rejected."""


class TimeoutError(LLMError):
    """Raised when a provider request times out."""


class ServerError(LLMError):
    """Raised when upstream returns a retryable server failure."""


class ProviderResponseError(LLMError):
    """Raised when a provider response shape cannot be parsed."""


class HTTPResponseLike(Protocol):
    """Minimal response protocol returned by async HTTP clients."""

    def json(self) -> object:
        """Return decoded JSON payload."""

    def raise_for_status(self) -> None:
        """Raise for non-successful HTTP responses."""


class AsyncHTTPClient(Protocol):
    """Minimal async HTTP client protocol used by providers."""

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
    ) -> HTTPResponseLike:
        """Send an HTTP POST request."""


class _HttpxAsyncClientAdapter:
    """Small adapter that keeps provider tests independent from httpx internals."""

    def __init__(self, *, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            return await client.post(url, headers=headers, json=json)


class LLMProvider(ABC):
    """Abstract provider contract used by review engines."""

    def __init__(
        self,
        config: LLMProviderConfig,
        *,
        http_client: AsyncHTTPClient | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        """Create an LLM provider instance."""

        if not config.base_url.strip():
            msg = "provider base_url must not be empty"
            raise ValueError(msg)
        if not config.api_key.strip():
            msg = "provider api_key must not be empty"
            raise ValueError(msg)
        if not config.model.strip():
            msg = "provider model must not be empty"
            raise ValueError(msg)
        if timeout_seconds <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)
        if max_retries < 0:
            msg = "max_retries must not be negative"
            raise ValueError(msg)

        self.config = config
        self._http_client = http_client or _HttpxAsyncClientAdapter(timeout_seconds=timeout_seconds)
        self._max_retries = max_retries

    @abstractmethod
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
    ) -> ChatResponse:
        """Return one complete chat response.

        Args:
            messages: 对话消息序列（可含 ``role="tool"`` 的工具结果回填）。
            tools: 可选的 function 定义列表。传入后 provider 以原生
                function calling 协议发送；返回的 ``ChatResponse.tool_calls``
                携带模型请求的工具调用。不支持 tools 的适配器应 fail-fast。
        """

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Reserved embedding contract for later semantic matching."""

        _ = texts
        msg = "embed() is reserved and not implemented yet"
        raise NotImplementedError(msg)

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        """Default streaming fallback splits a non-streaming response into chunks."""

        response = await self.chat(messages)
        for chunk in response.content.split():
            yield chunk

    async def _post_with_retry(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> HTTPResponseLike:
        """POST JSON with bounded exponential retry for transient failures."""

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http_client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return cast(HTTPResponseLike, response)
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise TimeoutError("LLM provider request timed out") from exc
            except httpx.HTTPStatusError as exc:
                mapped = _map_http_status(exc.response.status_code, exc)
                if isinstance(mapped, ServerError) and attempt < self._max_retries:
                    logger.warning("LLM provider server error; retrying attempt %s", attempt + 1)
                else:
                    raise mapped from exc
            await asyncio.sleep(min(0.1 * (2**attempt), 1.0))
        raise ServerError("LLM provider request failed after retries")


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible chat-completions provider adapter."""

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
    ) -> ChatResponse:
        """Call ``/chat/completions`` and normalize the response."""

        payload: dict[str, object] = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [_to_openai_message(message) for message in messages],
        }
        if tools:
            payload["tools"] = [_tool_spec_to_openai(tool) for tool in tools]
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens
        if self.config.default_json_mode:
            payload["response_format"] = {"type": "json_object"}

        # 透传 provider.extra 中约定的 extra_body 字段（如阿里百炼的 enable_thinking），
        # 未配置时不影响任何 provider。白名单方式避免把整个 extra 字典无差别注入。
        _extra_body = self.config.extra.get("extra_body")
        if isinstance(_extra_body, dict):
            payload.update(_extra_body)

        response = await self._post_with_retry(
            self.config.base_url.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                **self.config.extra_headers,
            },
            payload=payload,
        )
        data = _ensure_mapping(response.json())
        try:
            raw_message = data["choices"][0]["message"]
            content = raw_message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            msg = "OpenAI-compatible response missing choices[0].message.content"
            raise ProviderResponseError(msg) from exc
        if content is None:
            # 带 tool_calls 的响应里 content 可能为 null，归一化成空串。
            content = ""
        if not isinstance(content, str):
            msg = "OpenAI-compatible response content must be a string"
            raise ProviderResponseError(msg)
        if not isinstance(raw_message, dict):
            raw_message = {}
        return ChatResponse(
            content=content,
            model=self.config.model,
            tool_calls=_parse_openai_tool_calls(raw_message),
            usage=_normalise_usage(data.get("usage")),
            raw=dict(data),
        )


class AnthropicProvider(LLMProvider):
    """Anthropic native messages provider adapter."""

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
    ) -> ChatResponse:
        """Call Anthropic ``/messages`` and normalize the response."""

        system_messages = [message.content for message in messages if message.role == "system"]
        non_system_messages = [message for message in messages if message.role != "system"]
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": _to_anthropic_messages(non_system_messages),
            "max_tokens": self.config.max_tokens or 4096,
            "temperature": self.config.temperature,
        }
        if tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters or {"type": "object", "properties": {}},
                }
                for tool in tools
            ]
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)

        response = await self._post_with_retry(
            self.config.base_url.rstrip("/") + "/messages",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": str(self.config.extra.get("anthropic_version", "2023-06-01")),
                **self.config.extra_headers,
            },
            payload=payload,
        )
        data = _ensure_mapping(response.json())
        content, tool_calls = _extract_anthropic_content(data.get("content"))
        if not content and not tool_calls:
            msg = "Anthropic response did not contain text or tool_use content"
            raise ProviderResponseError(msg)
        return ChatResponse(
            content=content,
            model=self.config.model,
            tool_calls=tool_calls,
            usage=_normalise_usage(data.get("usage")),
            raw=dict(data),
        )


class CustomProvider(LLMProvider):
    """Custom HTTP JSON provider adapter with templated Authorization header."""

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
    ) -> ChatResponse:
        """POST provider-neutral JSON to a custom endpoint."""

        if tools:
            # Custom 协议没有约定的 function-calling 载荷格式，fail-fast 让
            # 调用方（agent 引擎）感知并降级，而不是静默丢掉工具能力。
            msg = "Custom provider does not support native tool calling"
            raise NotImplementedError(msg)

        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": self.config.temperature,
        }
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens

        response = await self._post_with_retry(
            self.config.base_url.rstrip("/"),
            headers={
                "Authorization": self._render_auth_header(),
                **self.config.extra_headers,
            },
            payload=payload,
        )
        data = _ensure_mapping(response.json())
        content = data.get("content")
        if not isinstance(content, str):
            msg = "Custom provider response must include string field 'content'"
            raise ProviderResponseError(msg)
        return ChatResponse(
            content=content,
            model=self.config.model,
            usage=_normalise_usage(data.get("usage")),
            raw=dict(data),
        )

    def _render_auth_header(self) -> str:
        template = str(self.config.extra.get("auth_header_template", "Bearer {api_key}"))
        return template.replace("{api_key}", self.config.api_key)


def count_tokens(text: str) -> int:
    """Return an approximate token count with a tiktoken-compatible fallback.

    The production deployment may install ``tiktoken`` for model-specific counts;
    tests and minimal installs fall back to whitespace tokenization.
    """

    try:
        tiktoken = importlib.import_module("tiktoken")
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:  # noqa: BLE001 - optional dependency fallback by design.
        return len(text.split())


def truncate_to_budget(messages: Sequence[ChatMessage], *, max_tokens: int) -> list[ChatMessage]:
    """Keep the most recent messages whose content fits within ``max_tokens``."""

    if max_tokens <= 0:
        msg = "max_tokens must be positive"
        raise ValueError(msg)

    kept_reversed: list[ChatMessage] = []
    remaining = max_tokens
    for message in reversed(messages):
        tokens = count_tokens(message.content)
        if tokens <= remaining:
            kept_reversed.append(message)
            remaining -= tokens
            continue
        if not kept_reversed:
            words = message.content.split()
            truncated = " ".join(words[:max_tokens])
            kept_reversed.append(message.model_copy(update={"content": truncated}))
        break
    return list(reversed(kept_reversed))


def _tool_spec_to_openai(tool: ToolSpec) -> dict[str, Any]:
    """Convert a neutral tool spec to the OpenAI ``tools`` payload shape."""

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters or {"type": "object", "properties": {}},
        },
    }


def _to_openai_message(message: ChatMessage) -> dict[str, Any]:
    """Serialize a neutral message into the OpenAI chat-completions shape."""

    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in message.tool_calls
        ]
    return payload


def _parse_openai_tool_calls(raw_message: dict[str, Any]) -> list[ToolCall]:
    """Extract ``tool_calls`` from an OpenAI response message（缺失则返回空）。"""

    calls: list[ToolCall] = []
    raw_calls = raw_message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return calls
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            arguments_text = arguments
        elif isinstance(arguments, dict):
            # 部分 OpenAI 兼容网关直接返回 dict，统一序列化成 JSON 字符串。
            arguments_text = json.dumps(arguments, ensure_ascii=False)
        else:
            arguments_text = "{}"
        calls.append(
            ToolCall(id=str(item.get("id") or ""), name=name, arguments=arguments_text),
        )
    return calls


def _to_anthropic_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """Serialize neutral messages into Anthropic ``messages`` blocks.

    转换规则：
    - assistant 消息带 ``tool_calls`` 时转成 ``tool_use`` content blocks；
    - ``role="tool"`` 消息转成带 ``tool_result`` blocks 的 user 消息，连续的
      tool 消息合并进同一条 user 消息（Anthropic 要求紧跟 tool_use 之后）；
    - 其余按 role 原样转 text content。
    """

    payload: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def _flush_tool_results() -> None:
        if pending_tool_results:
            payload.append({"role": "user", "content": pending_tool_results.copy()})
            pending_tool_results.clear()

    for message in messages:
        if message.role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id or "",
                    "content": message.content,
                },
            )
            continue
        _flush_tool_results()
        if message.role == "assistant" and message.tool_calls:
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                try:
                    tool_input = json.loads(call.arguments) if call.arguments else {}
                except ValueError:
                    tool_input = {"_raw": call.arguments}
                if not isinstance(tool_input, dict):
                    tool_input = {"_raw": call.arguments}
                blocks.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": tool_input},
                )
            payload.append({"role": "assistant", "content": blocks})
            continue
        payload.append({"role": message.role, "content": message.content})

    _flush_tool_results()
    return payload


def _extract_anthropic_content(value: object) -> tuple[str, list[ToolCall]]:
    """Split an Anthropic ``content`` block list into text and tool calls."""

    if not isinstance(value, list):
        msg = "Anthropic response content must be a list"
        raise ProviderResponseError(msg)
    chunks: list[str] = []
    calls: list[ToolCall] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        text = item.get("text")
        if item_type == "text" and isinstance(text, str):
            chunks.append(text)
            continue
        if item_type == "tool_use":
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            tool_input = item.get("input")
            if isinstance(tool_input, str):
                arguments_text = tool_input
            elif isinstance(tool_input, dict):
                arguments_text = json.dumps(tool_input, ensure_ascii=False)
            else:
                arguments_text = "{}"
            calls.append(
                ToolCall(
                    id=str(item.get("id") or ""),
                    name=name,
                    arguments=arguments_text,
                ),
            )
    return "".join(chunks), calls


def _map_http_status(status_code: int, exc: Exception) -> LLMError:
    if status_code == 429:
        return RateLimitError("LLM provider rate limit exceeded")
    if status_code in {401, 403}:
        return AuthError("LLM provider authentication failed")
    if status_code >= 500:
        return ServerError("LLM provider server error")
    return LLMError(f"LLM provider HTTP error: {status_code}")


def _ensure_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = "LLM provider response must be a JSON object"
        raise ProviderResponseError(msg)
    return cast(dict[str, Any], value)


def _normalise_usage(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    usage: dict[str, int] = {}
    for key, raw in value.items():
        if isinstance(key, str) and isinstance(raw, int):
            usage[key] = raw
    return usage
