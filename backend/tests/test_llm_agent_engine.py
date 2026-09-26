"""Tests for the agent-style ``llm-agent`` review engine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

import engines.llm_agent.engine as engine_module
from core.config import Settings
from engines.llm_agent.engine import LLMAgentEngine
from engines.llm_agent.trace import AgentRunTracer, build_tracer
from engines.types import DiffHunk, ProviderConfig, ReviewContext
from llm import LLMError
from llm.base import ChatMessage, ChatResponse, ToolCall, ToolSpec


@dataclass
class _FakeAgentClient:
    """按序返回预设 ChatResponse，并记录每轮调用的 messages/tools。"""

    responses: list[ChatResponse]
    turns: list[dict[str, Any]] = field(default_factory=list)
    plain_completions: list[str] = field(default_factory=list)

    async def complete_with_tools(
        self,
        *,
        provider: ProviderConfig,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> ChatResponse:
        self.turns.append(
            {
                "messages": list(messages),
                "tools": [tool.name for tool in tools or []],
                "system_prompt": system_prompt,
            }
        )
        return self.responses.pop(0)

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> str:
        self.plain_completions.append(prompt)
        return '{"findings": []}'


@dataclass
class _FakeRepoReader:
    """RepoReader 测试替身：记录调用并按文件返回预置内容。"""

    files: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def read_file(self, file_path: str, ref: str, *, max_chars: int = 8000) -> str:
        self.calls.append(("read_file", {"file_path": file_path, "ref": ref}))
        if file_path in self.files:
            return self.files[file_path]
        return f"[error] read_file({file_path}) failed: not found"

    async def list_tree(self, path: str, ref: str, *, recursive: bool = False) -> str:
        self.calls.append(("list_tree", {"path": path}))
        return "blob  src/app.py"

    async def blame(self, file_path: str, ref: str, *, max_chars: int = 6000) -> str:
        self.calls.append(("blame", {"file_path": file_path}))
        return "aaaa1111  L1  code"

    async def search(self, query: str, *, max_chars: int = 4000) -> str:
        self.calls.append(("search", {"query": query}))
        return "src/auth.py:10  def login()"

    async def commit_history(
        self, file_path: str, ref: str, limit: int = 10, *, max_chars: int = 3000
    ) -> str:
        self.calls.append(("commit_history", {"file_path": file_path}))
        return "abc123  2026-09-01  fix: bug"


def _provider(provider_type: str = "openai-compatible") -> ProviderConfig:
    return ProviderConfig(
        provider_id=uuid4(),
        provider_type=provider_type,
        base_url="https://llm.example.com/v1",
        model="reviewer-1",
        api_key="test-key",
    )


_UNSET: Any = object()


def _ctx(
    *,
    provider: Any = _UNSET,
    repo_reader: Any | None = None,
    diff_hunks: list[DiffHunk] | None = None,
) -> ReviewContext:
    return ReviewContext(
        review_id=uuid4(),
        project_id=uuid4(),
        mr_iid="42",
        source_branch="feature/login",
        target_branch="master",
        source_commit_sha="abc123",
        target_commit_sha="def456",
        diff_hunks=diff_hunks
        if diff_hunks is not None
        else [
            DiffHunk(
                file_path="app/auth.py",
                old_path="app/auth.py",
                new_start=10,
                new_lines=4,
                old_start=10,
                old_lines=3,
                content=(
                    "@@ -10,3 +10,4 @@ def login(user):\n"
                    " context = build_context(user)\n"
                    "+print(user.password)\n"
                    "+token = make_token(user)\n"
                    " return token\n"
                ),
            )
        ],
        provider=_provider() if provider is _UNSET else provider,
        repo_reader=repo_reader,
    )


def _final_response(findings_json: str = '{"findings": []}') -> ChatResponse:
    return ChatResponse(content=findings_json, model="reviewer-1")


def _tool_call_response(name: str = "read_file", arguments: str = "{}") -> ChatResponse:
    return ChatResponse(
        content="checking caller",
        model="reviewer-1",
        tool_calls=[ToolCall(id="call-1", name=name, arguments=arguments)],
    )


def _settings(**overrides: Any) -> Settings:
    return Settings(llm_filter_enabled=False, **overrides)


# ---- agent trace ----------------------------------------------------------------


@dataclass
class _RecordingSink:
    """TraceSink 测试替身：把事件攒进列表供断言。"""

    events: list[dict[str, Any]] = field(default_factory=list)

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _install_trace_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """把 engine.build_tracer 替换为带 recording sink 的 tracer，返回事件列表。"""

    sink = _RecordingSink()

    def fake_build_tracer(review_id: UUID, settings: Settings) -> AgentRunTracer:
        return AgentRunTracer(
            review_id=review_id,
            sinks=[sink],
            content_max_chars=settings.agent_trace_content_max_chars,
        )

    monkeypatch.setattr(engine_module, "build_tracer", fake_build_tracer)
    return sink.events


def _events_of(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event["event_type"] == event_type]


@pytest.mark.asyncio
async def test_agent_investigates_then_outputs_findings() -> None:
    """第一轮请求工具 -> 观察回填 -> 第二轮输出最终 findings JSON。"""

    reader = _FakeRepoReader(files={"app/auth.py": "def login(): ...\n"})
    client = _FakeAgentClient(
        responses=[
            _tool_call_response("read_file", '{"file_path": "app/auth.py"}'),
            _final_response(
                '{"findings": [{'
                '"file_path": "app/auth.py", "line_number": 11, '
                '"rule_id": "no-secret-logging", "severity": "BLOCKER", '
                '"title": "Password printed", "confidence": 0.9}]}'
            ),
        ]
    )
    engine = LLMAgentEngine(client=client, settings=_settings())

    findings = await engine.review(_ctx(repo_reader=reader))

    assert len(findings) == 1
    assert findings[0].file_path == "app/auth.py"
    assert findings[0].line_number == 11
    # 工具确实被执行过（未传 ref 时空串，由 reader 回退默认 ref）。
    assert ("read_file", {"file_path": "app/auth.py", "ref": ""}) in reader.calls
    second_turn_messages = client.turns[1]["messages"]
    assert any(m.role == "tool" for m in second_turn_messages)
    tool_msg = next(m for m in second_turn_messages if m.role == "tool")
    assert tool_msg.content == "def login(): ...\n"
    # 第一轮带全部工具，第二轮同样带工具（由模型决定何时结束）。
    assert client.turns[0]["tools"]
    assert client.turns[1]["tools"]


@pytest.mark.asyncio
async def test_agent_prompt_contains_diff_rules_and_tools_hint() -> None:
    reader = _FakeRepoReader()
    client = _FakeAgentClient(responses=[_final_response()])
    engine = LLMAgentEngine(client=client, settings=_settings())

    await engine.review(_ctx(repo_reader=reader))

    first_turn = client.turns[0]
    assert first_turn["system_prompt"] is not None
    assert "Investigation Strategy" in first_turn["system_prompt"]
    user_prompt = first_turn["messages"][0].content
    assert "print(user.password)" in user_prompt  # diff
    assert "## Active Rules" in user_prompt


@pytest.mark.asyncio
async def test_agent_returns_empty_when_provider_missing() -> None:
    client = _FakeAgentClient(responses=[])
    engine = LLMAgentEngine(client=client, settings=_settings())

    findings = await engine.review(_ctx(provider=None, repo_reader=_FakeRepoReader()))

    assert findings == []
    assert client.turns == []


@pytest.mark.asyncio
async def test_agent_returns_empty_when_repo_reader_missing() -> None:
    client = _FakeAgentClient(responses=[])
    engine = LLMAgentEngine(client=client, settings=_settings())

    findings = await engine.review(_ctx(repo_reader=None))

    assert findings == []
    assert client.turns == []


@pytest.mark.asyncio
async def test_agent_degrades_when_provider_rejects_tools() -> None:
    """custom 协议不支持原生 tools：fail-open 返回空，不误报审查失败。"""

    @dataclass
    class _RaisingClient:
        async def complete_with_tools(self, **kwargs: Any) -> ChatResponse:
            raise NotImplementedError("Custom provider does not support native tool calling")

        async def complete(self, **kwargs: Any) -> str:
            return '{"findings": []}'

    engine = LLMAgentEngine(client=_RaisingClient(), settings=_settings())

    findings = await engine.review(_ctx(repo_reader=_FakeRepoReader()))

    assert findings == []


@pytest.mark.asyncio
async def test_agent_empty_diff_skips_llm_entirely() -> None:
    client = _FakeAgentClient(responses=[])
    engine = LLMAgentEngine(client=client, settings=_settings())

    findings = await engine.review(_ctx(repo_reader=_FakeRepoReader(), diff_hunks=[]))

    assert findings == []
    assert client.turns == []


@pytest.mark.asyncio
async def test_agent_unknown_tool_and_malformed_args_become_error_observations() -> None:
    reader = _FakeRepoReader()
    client = _FakeAgentClient(
        responses=[
            _tool_call_response("nonexistent_tool", "{}"),
            _tool_call_response("read_file", "not-json"),
            _final_response(),
        ]
    )
    engine = LLMAgentEngine(client=client, settings=_settings())

    findings = await engine.review(_ctx(repo_reader=reader))

    assert findings == []
    tool_messages = [
        m.content
        for turn in client.turns
        for m in turn["messages"]
        if m.role == "tool"
    ]
    assert any(content.startswith("[error] unknown tool") for content in tool_messages)
    assert any(content.startswith("[error] malformed arguments") for content in tool_messages)
    # 非法工具调用不应该执行任何真实工具。
    assert reader.calls == []


@pytest.mark.asyncio
async def test_agent_tool_error_observation_does_not_abort_review() -> None:
    """工具执行抛异常 -> 错误观察回填，循环继续到最终输出。"""

    @dataclass
    class _ExplodingToolReader:
        calls: list = field(default_factory=list)

        async def read_file(self, file_path: str, ref: str, *, max_chars: int = 8000) -> str:
            raise RuntimeError("gitlab down")

        async def list_tree(self, path: str, ref: str, *, recursive: bool = False) -> str:
            return "blob  x.py"

        async def blame(self, file_path: str, ref: str, *, max_chars: int = 6000) -> str:
            return ""

        async def search(self, query: str, *, max_chars: int = 4000) -> str:
            return ""

        async def commit_history(
            self, file_path: str, ref: str, limit: int = 10, *, max_chars: int = 3000
        ) -> str:
            return ""

    client = _FakeAgentClient(
        responses=[
            _tool_call_response("read_file", '{"file_path": "app/auth.py"}'),
            _final_response(),
        ]
    )
    engine = LLMAgentEngine(client=client, settings=_settings())

    findings = await engine.review(_ctx(repo_reader=_ExplodingToolReader()))

    assert findings == []
    tool_messages = [
        m.content for turn in client.turns for m in turn["messages"] if m.role == "tool"
    ]
    assert any("[error] tool read_file failed" in content for content in tool_messages)


@pytest.mark.asyncio
async def test_agent_forces_final_json_when_max_turns_reached() -> None:
    """模型一直请求工具 -> 轮数耗尽后不带 tools 强制收口。"""

    reader = _FakeRepoReader()
    client = _FakeAgentClient(
        responses=[
            _tool_call_response("read_file", '{"file_path": "app/auth.py"}'),
            _tool_call_response("search_code", '{"query": "login"}'),
            # 轮数耗尽后的强制收口调用。
            _final_response(),
        ]
    )
    engine = LLMAgentEngine(client=client, settings=_settings(agent_max_turns=2))

    findings = await engine.review(_ctx(repo_reader=reader))

    assert findings == []
    # 最后一轮不带 tools（强制收口）。
    assert client.turns[-1]["tools"] == []
    # 收口指令并入最后一条 tool 观察消息（保持消息交替）。
    last_tool_msg = next(
        m for m in reversed(client.turns[-1]["messages"]) if m.role == "tool"
    )
    assert "Investigation budget reached" in last_tool_msg.content


@pytest.mark.asyncio
async def test_agent_budget_exhaustion_forces_wrap_up() -> None:
    """观察总预算耗尽 -> 同轮后续工具调用直接返回预算错误观察并收口。"""

    reader = _FakeRepoReader()
    # 一轮内两个工具调用：第一个把预算吃完，第二个拿到预算错误观察。
    exhausted_response = ChatResponse(
        content="checking two things",
        model="reviewer-1",
        tool_calls=[
            ToolCall(id="call-1", name="read_file", arguments='{"file_path": "app/auth.py"}'),
            ToolCall(id="call-2", name="search_code", arguments='{"query": "login"}'),
        ],
    )
    client = _FakeAgentClient(
        responses=[
            exhausted_response,
            _final_response(),
        ]
    )
    engine = LLMAgentEngine(
        client=client,
        settings=_settings(
            agent_max_turns=4,
            agent_total_context_max_chars=10,
        ),
    )

    await engine.review(_ctx(repo_reader=reader))

    tool_messages = [
        m.content for turn in client.turns for m in turn["messages"] if m.role == "tool"
    ]
    assert any("context budget exhausted" in content for content in tool_messages)
    assert client.turns[-1]["tools"] == []


@pytest.mark.asyncio
async def test_agent_final_response_not_json_returns_empty() -> None:
    client = _FakeAgentClient(
        responses=[ChatResponse(content="I think the code looks fine.", model="reviewer-1")]
    )
    engine = LLMAgentEngine(client=client, settings=_settings())

    findings = await engine.review(_ctx(repo_reader=_FakeRepoReader()))

    assert findings == []


@pytest.mark.asyncio
async def test_agent_health_check_reports_metadata() -> None:
    engine = LLMAgentEngine(client=_FakeAgentClient(responses=[]), settings=_settings())

    status = await engine.health_check()

    assert status.status == "ok"
    assert status.details["implementation"] == "llm-agent"
    assert status.details["requires_repo_clone"] is False


@pytest.mark.asyncio
async def test_agent_registered_engine_name_is_stable() -> None:
    engine = LLMAgentEngine(client=_FakeAgentClient(responses=[]), settings=_settings())

    assert engine.name() == "llm-agent"
    assert engine.supports_feedback() is True


@pytest.mark.asyncio
async def test_agent_finding_out_of_diff_is_dropped() -> None:
    client = _FakeAgentClient(
        responses=[
            _final_response(
                '{"findings": [{'
                '"file_path": "other/elsewhere.py", "line_number": 1, '
                '"rule_id": "x", "severity": "WARNING", "title": "out of diff"}]}'
            )
        ]
    )
    engine = LLMAgentEngine(client=client, settings=_settings())

    findings = await engine.review(_ctx(repo_reader=_FakeRepoReader()))

    assert findings == []


# ---- agent trace 事件断言 --------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_trace_records_full_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """完整生命周期：run_started -> 每轮 llm_response/tool_executed -> run_finished。"""

    events = _install_trace_recorder(monkeypatch)
    reader = _FakeRepoReader(files={"app/auth.py": "def login(): ...\n"})
    client = _FakeAgentClient(
        responses=[
            _tool_call_response("read_file", '{"file_path": "app/auth.py"}'),
            _final_response('{"findings": []}'),
        ]
    )
    engine = LLMAgentEngine(client=client, settings=_settings())
    ctx = _ctx(repo_reader=reader)

    await engine.review(ctx)

    assert [event["event_type"] for event in events] == [
        "run_started",
        "llm_response",
        "tool_executed",
        "llm_response",
        "run_finished",
    ]
    # seq 从 1 起严格递增，review_id 全程一致。
    assert [event["seq"] for event in events] == [1, 2, 3, 4, 5]
    assert {event["review_id"] for event in events} == {str(ctx.review_id)}

    started = events[0]["payload"]
    assert started["model"] == "reviewer-1"
    assert started["max_turns"] == 8
    assert "read_file" in started["tool_names"]
    assert started["budget_max_chars"] == 120000
    assert started["system_prompt_chars"] > 0
    assert started["user_prompt_chars"] > 0

    first_response = events[1]
    assert first_response["turn"] == 0
    assert first_response["payload"]["tool_calls"] == [
        {"id": "call-1", "name": "read_file", "arguments": '{"file_path": "app/auth.py"}'}
    ]
    assert first_response["duration_ms"] is not None

    tool_event = events[2]
    assert tool_event["tool_name"] == "read_file"
    assert tool_event["status"] == "ok"
    assert tool_event["payload"]["arguments"] == {"file_path": "app/auth.py"}
    assert tool_event["payload"]["output"] == "def login(): ...\n"
    assert tool_event["duration_ms"] is not None
    assert tool_event["payload"]["budget_used"] == len("def login(): ...\n")

    final_response = events[3]
    assert final_response["turn"] == 1
    assert final_response["payload"]["tool_calls"] == []
    assert final_response["payload"]["is_closeout"] is False

    finished = events[4]["payload"]
    assert finished["findings_count"] == 0
    assert finished["findings_before_filter"] == 0
    assert finished["turns_used"] == 2
    assert finished["filter_applied"] is False
    assert "findings" in (finished["final_text"] or "")


@pytest.mark.asyncio
async def test_agent_trace_records_error_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法工具/非法参数在 trace 里保留具体 status，且审查照常收口。"""

    events = _install_trace_recorder(monkeypatch)
    reader = _FakeRepoReader()
    client = _FakeAgentClient(
        responses=[
            _tool_call_response("nonexistent_tool", "{}"),
            _tool_call_response("read_file", "not-json"),
            _final_response(),
        ]
    )
    engine = LLMAgentEngine(client=client, settings=_settings())

    await engine.review(_ctx(repo_reader=reader))

    tool_events = _events_of(events, "tool_executed")
    assert [event["status"] for event in tool_events] == [
        "unknown_tool",
        "malformed_arguments",
    ]
    assert tool_events[0]["payload"]["output"].startswith("[error] unknown tool")
    assert tool_events[1]["payload"]["output"].startswith("[error] malformed arguments")
    assert reader.calls == []
    assert _events_of(events, "run_finished"), "错误观察不应中断 run_finished"


@pytest.mark.asyncio
async def test_agent_trace_records_budget_exhausted_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _install_trace_recorder(monkeypatch)
    reader = _FakeRepoReader(files={"app/auth.py": "x" * 50})
    exhausted_response = ChatResponse(
        content="checking two things",
        model="reviewer-1",
        tool_calls=[
            ToolCall(id="call-1", name="read_file", arguments='{"file_path": "app/auth.py"}'),
            ToolCall(id="call-2", name="search_code", arguments='{"query": "login"}'),
        ],
    )
    client = _FakeAgentClient(responses=[exhausted_response, _final_response()])
    engine = LLMAgentEngine(
        client=client,
        settings=_settings(agent_max_turns=4, agent_total_context_max_chars=10),
    )

    await engine.review(_ctx(repo_reader=reader))

    tool_events = _events_of(events, "tool_executed")
    assert [event["status"] for event in tool_events] == ["ok", "budget_exhausted"]
    assert tool_events[1]["payload"]["output"].startswith("[error] context budget exhausted")


@pytest.mark.asyncio
async def test_agent_trace_records_timeout_and_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工具抛异常 -> error；超时 -> timeout，都能从 trace 看出真实原因。"""

    events = _install_trace_recorder(monkeypatch)

    @dataclass
    class _SlowAndExplodingReader:
        calls: list = field(default_factory=list)

        async def read_file(self, file_path: str, ref: str, *, max_chars: int = 8000) -> str:
            self.calls.append(file_path)
            if len(self.calls) == 1:
                raise RuntimeError("gitlab down")
            await asyncio.sleep(1.0)
            return "never"

        async def list_tree(self, path: str, ref: str, *, recursive: bool = False) -> str:
            return ""

        async def blame(self, file_path: str, ref: str, *, max_chars: int = 6000) -> str:
            return ""

        async def search(self, query: str, *, max_chars: int = 4000) -> str:
            return ""

        async def commit_history(
            self, file_path: str, ref: str, limit: int = 10, *, max_chars: int = 3000
        ) -> str:
            return ""

    reader = _SlowAndExplodingReader()
    client = _FakeAgentClient(
        responses=[
            _tool_call_response("read_file", '{"file_path": "a.py"}'),
            _tool_call_response("read_file", '{"file_path": "b.py"}'),
            _final_response(),
        ]
    )
    engine = LLMAgentEngine(client=client, settings=_settings(agent_tool_timeout_seconds=0.1))

    await engine.review(_ctx(repo_reader=reader))

    tool_events = _events_of(events, "tool_executed")
    assert [event["status"] for event in tool_events] == ["error", "timeout"]
    assert "gitlab down" in tool_events[0]["payload"]["output"]
    assert "timed out" in tool_events[1]["payload"]["output"]


@pytest.mark.asyncio
async def test_agent_trace_run_failed_on_invalid_final_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _install_trace_recorder(monkeypatch)
    client = _FakeAgentClient(
        responses=[ChatResponse(content="not json", model="reviewer-1")]
    )
    engine = LLMAgentEngine(client=client, settings=_settings())

    await engine.review(_ctx(repo_reader=_FakeRepoReader()))

    assert events[-1]["event_type"] == "run_failed"
    assert events[-1]["payload"]["reason"] == "invalid_final_json"
    assert _events_of(events, "run_finished") == []


@pytest.mark.asyncio
async def test_agent_trace_run_failed_on_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass
    class _FailingClient:
        async def complete_with_tools(self, **kwargs: Any) -> ChatResponse:
            raise LLMError("provider unreachable")

        async def complete(self, **kwargs: Any) -> str:
            return '{"findings": []}'

    events = _install_trace_recorder(monkeypatch)
    engine = LLMAgentEngine(client=_FailingClient(), settings=_settings())

    await engine.review(_ctx(repo_reader=_FakeRepoReader()))

    assert [event["event_type"] for event in events] == ["run_started", "run_failed"]
    assert events[-1]["payload"]["reason"] == "llm_error"
    assert "provider unreachable" in (events[-1]["payload"]["error"] or "")


@pytest.mark.asyncio
async def test_agent_trace_marks_forced_closeout(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _install_trace_recorder(monkeypatch)
    reader = _FakeRepoReader()
    client = _FakeAgentClient(
        responses=[
            _tool_call_response("read_file", '{"file_path": "app/auth.py"}'),
            _tool_call_response("search_code", '{"query": "login"}'),
            _final_response(),
        ]
    )
    engine = LLMAgentEngine(client=client, settings=_settings(agent_max_turns=2))

    await engine.review(_ctx(repo_reader=reader))

    llm_events = _events_of(events, "llm_response")
    assert [event["payload"]["is_closeout"] for event in llm_events] == [False, False, True]
    finished = _events_of(events, "run_finished")[0]["payload"]
    assert finished["turns_used"] == 2


def test_build_tracer_sinks_follow_settings() -> None:
    review_id = uuid4()
    both = build_tracer(
        review_id, Settings(agent_trace_enabled=True, agent_trace_db_enabled=True)
    )
    assert [type(sink).__name__ for sink in both.sinks] == ["LogSink", "DbSink"]
    log_only = build_tracer(
        review_id, Settings(agent_trace_enabled=True, agent_trace_db_enabled=False)
    )
    assert [type(sink).__name__ for sink in log_only.sinks] == ["LogSink"]
    none = build_tracer(
        review_id, Settings(agent_trace_enabled=False, agent_trace_db_enabled=False)
    )
    assert none.sinks == []
