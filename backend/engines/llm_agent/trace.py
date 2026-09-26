"""llm-agent 执行轨迹采集：结构化 stdout 日志 + 可选落库。

每次 ``LLMAgentEngine.review`` 创建一个 :class:`AgentRunTracer`，引擎在
关键节点调用其 ``run_started / llm_response / tool_executed / run_finished /
run_failed``，tracer 组装事件后 fan-out 到各 sink：

- :class:`LogSink`：走现有 ``JsonFormatter``，extra 顶层带 review_id/
  event_type/turn/tool_name/status/duration_ms，大字段截短（完整内容由
  DbSink 承担）；
- :class:`DbSink`：逐事件写入 ``agent_trace_events`` 表（best-effort，
  失败闩锁关闭，遵循引擎层的 fail-open 契约，绝不拖垮审查流程）。

trace 是纯旁路：任何 sink 抛异常都不影响 review 主流程。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from core.config import Settings

logger = logging.getLogger(__name__)

# stdout 日志里字符串字段的截断长度；完整内容走 DbSink 入库。
_LOG_FIELD_MAX_CHARS = 500

EVENT_RUN_STARTED = "run_started"
EVENT_LLM_RESPONSE = "llm_response"
EVENT_TOOL_EXECUTED = "tool_executed"
EVENT_RUN_FINISHED = "run_finished"
EVENT_RUN_FAILED = "run_failed"

# 工具执行状态归类（引擎侧把失败归一化为错误观察，trace 需要还原原因）。
STATUS_OK = "ok"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
STATUS_MALFORMED_ARGUMENTS = "malformed_arguments"
STATUS_UNKNOWN_TOOL = "unknown_tool"
STATUS_BUDGET_EXHAUSTED = "budget_exhausted"


def _clip(text: str | None, max_chars: int) -> str | None:
    if text is None or len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... (truncated, total {len(text)} chars)"


def _clip_structured(value: Any, max_chars: int) -> Any:  # noqa: ANN401 - 透传任意 payload 结构
    """递归截断结构里的字符串字段（dict/list 浅层遍历已覆盖 payload 形态）。"""

    if isinstance(value, str):
        return _clip(value, max_chars)
    if isinstance(value, dict):
        return {key: _clip_structured(item, max_chars) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_structured(item, max_chars) for item in value]
    return value


class TraceSink(Protocol):
    """trace 事件出口协议。"""

    async def emit(self, event: dict[str, Any]) -> None:
        """接收一条完整事件：review_id/seq/event_type/turn/tool_name/status/duration_ms/payload。"""


class LogSink:
    """把事件打到现有 JSON stdout 日志（extra 顶层字段 + 嵌套 payload）。"""

    async def emit(self, event: dict[str, Any]) -> None:
        extra = {
            key: _clip_structured(value, _LOG_FIELD_MAX_CHARS)
            for key, value in event.items()
        }
        logger.info("agent trace event", extra=extra)


class DbSink:
    """把事件写入 ``agent_trace_events`` 表；失败闩锁关闭，只告警一次。"""

    def __init__(self) -> None:
        self._disabled = False

    async def emit(self, event: dict[str, Any]) -> None:
        if self._disabled:
            return
        try:
            # 惰性导入 + 属性访问：engine 在 import 时构建 sink，这里必须等
            # 运行时再取 db.AsyncSessionLocal，才能吃到测试的 monkeypatch。
            from core import db
            from models.agent_trace_event import AgentTraceEvent

            session_factory = getattr(db, "AsyncSessionLocal", None)
            if session_factory is None:
                self._disable_once("agent trace db sink disabled: db.AsyncSessionLocal missing")
                return
            row = AgentTraceEvent(
                review_id=UUID(str(event["review_id"])),
                seq=event["seq"],
                turn=event.get("turn"),
                event_type=event["event_type"],
                tool_name=event.get("tool_name"),
                status=event.get("status"),
                duration_ms=event.get("duration_ms"),
                payload=event.get("payload"),
            )
            async with session_factory() as session:
                session.add(row)
                await session.commit()
        except Exception:  # noqa: BLE001 - trace 是旁路，失败不能影响审查
            self._disable_once(
                "agent trace db sink disabled after write failure",
                exc_info=True,
            )

    def _disable_once(self, message: str, *, exc_info: bool = False) -> None:
        if self._disabled:
            return
        self._disabled = True
        logger.warning(message, exc_info=exc_info)


@dataclass
class AgentRunTracer:
    """单次 review 的轨迹记录器：维护 seq 序号并 fan-out 到 sinks。"""

    review_id: UUID
    sinks: list[TraceSink] = field(default_factory=list)
    content_max_chars: int = 20000
    started_at: float = field(default_factory=time.perf_counter)
    # 引擎侧回填的 run 级状态：run_finished 事件直接取用。
    turns_used: int = 0
    final_text: str | None = None
    failed: bool = False
    _seq: int = 0

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started_at) * 1000)

    async def _emit(
        self,
        event_type: str,
        *,
        turn: int | None = None,
        tool_name: str | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._seq += 1
        event = {
            "review_id": str(self.review_id),
            "seq": self._seq,
            "event_type": event_type,
            "turn": turn,
            "tool_name": tool_name,
            "status": status,
            "duration_ms": duration_ms,
            "payload": payload or {},
        }
        for sink in self.sinks:
            await sink.emit(event)

    async def run_started(
        self,
        *,
        provider_type: str,
        model: str,
        max_turns: int,
        tool_names: list[str],
        system_prompt_chars: int,
        user_prompt_chars: int,
        budget_max_chars: int,
    ) -> None:
        await self._emit(
            EVENT_RUN_STARTED,
            payload={
                "provider_type": provider_type,
                "model": model,
                "max_turns": max_turns,
                "tool_names": tool_names,
                "system_prompt_chars": system_prompt_chars,
                "user_prompt_chars": user_prompt_chars,
                "budget_max_chars": budget_max_chars,
            },
        )

    async def llm_response(
        self,
        *,
        turn: int,
        content: str,
        tool_calls: list[dict[str, str]],
        usage: dict[str, int],
        model: str,
        message_count: int,
        duration_ms: int,
        is_closeout: bool = False,
    ) -> None:
        await self._emit(
            EVENT_LLM_RESPONSE,
            turn=turn,
            duration_ms=duration_ms,
            payload={
                "content": _clip(content, self.content_max_chars),
                "tool_calls": tool_calls,
                "usage": usage,
                "model": model,
                "message_count": message_count,
                "is_closeout": is_closeout,
            },
        )

    async def tool_executed(
        self,
        *,
        turn: int,
        call_id: str,
        name: str,
        raw_arguments: str,
        arguments: dict[str, Any] | None,
        output: str,
        status: str,
        duration_ms: int | None,
        budget_used: int,
        budget_max_chars: int,
    ) -> None:
        await self._emit(
            EVENT_TOOL_EXECUTED,
            turn=turn,
            tool_name=name,
            status=status,
            duration_ms=duration_ms,
            payload={
                "call_id": call_id,
                "raw_arguments": _clip(raw_arguments, self.content_max_chars),
                "arguments": arguments,
                "output": _clip(output, self.content_max_chars),
                "budget_used": budget_used,
                "budget_max_chars": budget_max_chars,
            },
        )

    async def run_finished(
        self,
        *,
        findings_count: int,
        filter_applied: bool,
        findings_before_filter: int | None = None,
    ) -> None:
        await self._emit(
            EVENT_RUN_FINISHED,
            duration_ms=self.elapsed_ms,
            payload={
                "turns_used": self.turns_used,
                "findings_count": findings_count,
                "findings_before_filter": findings_before_filter,
                "final_text": _clip(self.final_text, self.content_max_chars),
                "filter_applied": filter_applied,
            },
        )

    async def run_failed(
        self,
        *,
        reason: str,
        error: str | None = None,
        turn: int | None = None,
    ) -> None:
        self.failed = True
        await self._emit(
            EVENT_RUN_FAILED,
            turn=turn,
            duration_ms=self.elapsed_ms,
            payload={
                "reason": reason,
                "error": error,
                "turns_used": self.turns_used,
            },
        )


def build_tracer(review_id: UUID, settings: Settings) -> AgentRunTracer:
    """按配置组装 tracer；两项都关闭时返回无 sink 的空转 tracer。"""

    sinks: list[TraceSink] = []
    if settings.agent_trace_enabled:
        sinks.append(LogSink())
    if settings.agent_trace_db_enabled:
        sinks.append(DbSink())
    return AgentRunTracer(
        review_id=review_id,
        sinks=sinks,
        content_max_chars=settings.agent_trace_content_max_chars,
    )
