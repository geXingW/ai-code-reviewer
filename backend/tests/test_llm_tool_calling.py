"""Tests for native tool calling across LLM provider adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from llm.base import (
    AnthropicProvider,
    ChatMessage,
    CustomProvider,
    LLMProviderConfig,
    OpenAICompatibleProvider,
    ToolCall,
    ToolSpec,
)


@dataclass
class _FakeHTTPResponse:
    payload: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return self.payload

    def raise_for_status(self) -> None:
        """No-op for successful fake responses."""


@dataclass
class _FakeHTTPClient:
    """Capture provider HTTP requests; returns queued JSON responses."""

    responses: list[_FakeHTTPResponse]
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeHTTPResponse:
        self.requests.append({"url": url, "headers": headers or {}, "json": json or {}})
        return self.responses.pop(0)


def _config(protocol: str = "openai_compatible") -> LLMProviderConfig:
    return LLMProviderConfig(
        provider_id=uuid4(),
        protocol=protocol,  # type: ignore[arg-type]
        base_url="https://llm.example.com/v1",
        api_key="test-key",
        model="reviewer-1",
    )


_SEARCH_TOOL = ToolSpec(
    name="search_code",
    description="Search project code",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)


class TestOpenAICompatibleToolCalling:
    async def test_tools_sent_in_openai_payload_shape(self) -> None:
        http = _FakeHTTPClient(
            responses=[
                _FakeHTTPResponse(
                    payload={"choices": [{"message": {"content": "done"}}]},
                )
            ]
        )
        provider = OpenAICompatibleProvider(_config(), http_client=http)

        response = await provider.chat(
            [ChatMessage(role="user", content="review")],
            tools=[_SEARCH_TOOL],
        )

        assert response.content == "done"
        payload = http.requests[0]["json"]
        assert payload["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "search_code",
                    "description": "Search project code",
                    "parameters": _SEARCH_TOOL.parameters,
                },
            }
        ]

    async def test_no_tools_keeps_payload_unchanged(self) -> None:
        http = _FakeHTTPClient(
            responses=[
                _FakeHTTPResponse(
                    payload={"choices": [{"message": {"content": "ok"}}]},
                )
            ]
        )
        provider = OpenAICompatibleProvider(_config(), http_client=http)

        await provider.chat([ChatMessage(role="user", content="review")])

        assert "tools" not in http.requests[0]["json"]

    async def test_tool_calls_round_trip(self) -> None:
        http = _FakeHTTPClient(
            responses=[
                _FakeHTTPResponse(
                    payload={
                        "choices": [
                            {
                                "message": {
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call-1",
                                            "type": "function",
                                            "function": {
                                                "name": "search_code",
                                                "arguments": '{"query": "login"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ],
                    },
                )
            ]
        )
        provider = OpenAICompatibleProvider(_config(), http_client=http)

        response = await provider.chat(
            [ChatMessage(role="user", content="review")],
            tools=[_SEARCH_TOOL],
        )

        assert response.content == ""
        assert response.tool_calls == [
            ToolCall(id="call-1", name="search_code", arguments='{"query": "login"}'),
        ]

    async def test_tool_result_and_assistant_tool_calls_serialized(self) -> None:
        http = _FakeHTTPClient(
            responses=[
                _FakeHTTPResponse(
                    payload={"choices": [{"message": {"content": "{}"}}]},
                )
            ]
        )
        provider = OpenAICompatibleProvider(_config(), http_client=http)
        messages = [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call-1", name="search_code", arguments="{}")],
            ),
            ChatMessage(role="tool", tool_call_id="call-1", content="[result] hit"),
        ]

        await provider.chat(messages, tools=[_SEARCH_TOOL])

        sent = http.requests[0]["json"]["messages"]
        assert sent[0]["tool_calls"][0] == {
            "id": "call-1",
            "type": "function",
            "function": {"name": "search_code", "arguments": "{}"},
        }
        assert sent[1] == {"role": "tool", "content": "[result] hit", "tool_call_id": "call-1"}

    async def test_dict_arguments_normalized_to_json_string(self) -> None:
        http = _FakeHTTPClient(
            responses=[
                _FakeHTTPResponse(
                    payload={
                        "choices": [
                            {
                                "message": {
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "function": {
                                                "name": "read_file",
                                                "arguments": {"file_path": "app.py"},
                                            },
                                        }
                                    ],
                                }
                            }
                        ],
                    },
                )
            ]
        )
        provider = OpenAICompatibleProvider(_config(), http_client=http)

        response = await provider.chat([ChatMessage(role="user", content="x")])

        assert response.tool_calls[0].arguments == json.dumps({"file_path": "app.py"})


class TestAnthropicToolCalling:
    async def test_tools_sent_as_input_schema(self) -> None:
        http = _FakeHTTPClient(
            responses=[
                _FakeHTTPResponse(
                    payload={"content": [{"type": "text", "text": "done"}]},
                )
            ]
        )
        provider = AnthropicProvider(_config("anthropic"), http_client=http)

        response = await provider.chat(
            [ChatMessage(role="user", content="review")],
            tools=[_SEARCH_TOOL],
        )

        assert response.content == "done"
        payload = http.requests[0]["json"]
        assert payload["tools"] == [
            {
                "name": "search_code",
                "description": "Search project code",
                "input_schema": _SEARCH_TOOL.parameters,
            }
        ]

    async def test_tool_use_response_parsed(self) -> None:
        http = _FakeHTTPClient(
            responses=[
                _FakeHTTPResponse(
                    payload={
                        "content": [
                            {"type": "text", "text": "let me check"},
                            {
                                "type": "tool_use",
                                "id": "toolu-1",
                                "name": "read_file",
                                "input": {"file_path": "app.py"},
                            },
                        ]
                    },
                )
            ]
        )
        provider = AnthropicProvider(_config("anthropic"), http_client=http)

        response = await provider.chat(
            [ChatMessage(role="user", content="x")],
            tools=[_SEARCH_TOOL],
        )

        assert response.content == "let me check"
        assert response.tool_calls == [
            ToolCall(
                id="toolu-1",
                name="read_file",
                arguments=json.dumps({"file_path": "app.py"}),
            ),
        ]

    async def test_neutral_messages_become_anthropic_blocks(self) -> None:
        http = _FakeHTTPClient(
            responses=[
                _FakeHTTPResponse(
                    payload={"content": [{"type": "text", "text": "{}"}]},
                )
            ]
        )
        provider = AnthropicProvider(_config("anthropic"), http_client=http)
        messages = [
            ChatMessage(
                role="assistant",
                content="checking",
                tool_calls=[ToolCall(id="toolu-1", name="read_file", arguments="{}")],
            ),
            ChatMessage(role="tool", tool_call_id="toolu-1", content="[result] code"),
            ChatMessage(role="tool", tool_call_id="toolu-2", content="[result] more"),
        ]

        await provider.chat(messages, tools=[_SEARCH_TOOL])

        sent = http.requests[0]["json"]["messages"]
        assert sent[0] == {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "toolu-1", "name": "read_file", "input": {}},
            ],
        }
        # 连续 tool 消息合并成一条 user 消息内的两个 tool_result block。
        assert sent[1] == {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu-1", "content": "[result] code"},
                {"type": "tool_result", "tool_use_id": "toolu-2", "content": "[result] more"},
            ],
        }


class TestCustomProviderToolCalling:
    async def test_tools_fail_fast_with_not_implemented(self) -> None:
        http = _FakeHTTPClient(responses=[])
        provider = CustomProvider(_config("custom"), http_client=http)

        with pytest.raises(NotImplementedError):
            await provider.chat(
                [ChatMessage(role="user", content="x")],
                tools=[_SEARCH_TOOL],
            )

        # 未发任何请求。
        assert http.requests == []
