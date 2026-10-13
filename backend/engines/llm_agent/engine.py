"""Agent-style LLM review engine driven by native tool calling.

``LLMAgentEngine`` 与 ``LLMDirectEngine`` 共享 finding 解析层
（:mod:`engines.finding_parsing`）与可选的 filter 阶段（组合一个
``LLMDirectEngine`` 复用其 ``_filter_findings``），但审查主体是一个
多轮 function-calling 循环：

1. 首轮 user prompt 携带 MR 上下文 + 规则 + 负例历史 + diff；
2. 模型返回 ``tool_calls`` 时执行只读工具并把观察以 ``role="tool"``
   回填，进入下一轮；
3. 模型返回纯文本（最终 JSON）或轮数/预算耗尽时结束；
4. 解析 findings，做误报历史过滤（解析层内置）与可选 filter 证伪。

Fail-open 契约：provider 缺失、``repo_reader`` 缺失、provider 不支持
原生 tools（custom 协议）、LLM 调用失败、最终响应不可解析——一律打
warning 并返回空/尽力 findings，不抛异常拖垮整体审查。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Protocol

from core.config import Settings, get_settings
from engines.base import ReviewEngine
from engines.finding_parsing import parse_findings_from_response
from engines.llm_agent.tools import (
    AgentTool,
    build_default_tools,
    parse_tool_arguments,
    tool_to_spec,
)
from engines.llm_agent.trace import (
    STATUS_BUDGET_EXHAUSTED,
    STATUS_CACHED,
    STATUS_ERROR,
    STATUS_MALFORMED_ARGUMENTS,
    STATUS_OK,
    STATUS_TIMEOUT,
    STATUS_UNKNOWN_TOOL,
    AgentRunTracer,
    build_tracer,
)
from engines.llm_engine.engine import (
    LLMDirectEngine,
    _load_global_prompt,
    _load_negative_prompt,
    _render_template,
)
from engines.registry import register_engine
from engines.types import (
    Finding,
    HealthStatus,
    ProviderConfig,
    ReviewContext,
    SkippedFile,
)
from llm import (
    AsyncHTTPClient,
    ChatMessage,
    ChatResponse,
    LLMError,
    ToolCall,
    ToolSpec,
    build_provider,
)

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_FORCE_FINAL_TEXT = (
    "\n\n[instruction] Investigation budget reached. Based on the information "
    'gathered so far, output the final findings JSON now. If none, output '
    '{"findings": []}. Do not request any more tools.'
)

# 循环熔断纠正提示：连续 N 轮工具调用全部命中去重缓存时注入。
_STALL_WARNING_TEXT = (
    "\n\n[notice] All tool calls in the last {turns} turns were duplicates of "
    "earlier calls — you are going in circles. Change strategy: read a "
    "different file or region (read_file line_start/line_end), search a "
    "different identifier, or output the final findings JSON now with the "
    "evidence you already have."
)


@cache
def _load_prompt(name: str) -> str:
    """从 prompts/ 目录读取 agent prompt 模板。"""

    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


class AgentLLMClient(Protocol):
    """Minimal client contract the agent engine needs."""

    async def complete_with_tools(
        self,
        *,
        provider: ProviderConfig,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> ChatResponse:
        """One chat turn, optionally advertising tools to the model."""
        ...

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> str:
        """Plain completion without tools（供 filter 阶段复用）。"""
        ...


class ProviderAgentClient:
    """Provider-backed agent client built on the shared LLM abstraction."""

    def __init__(
        self,
        *,
        http_client: AsyncHTTPClient | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        """Create an agent client.

        Args:
            http_client: Optional provider HTTP transport for tests.
            timeout_seconds: Optional per-request 超时；``None`` 走 provider 默认。
            max_retries: Optional 重试上限；``None`` 走 provider 默认。
        """

        self._http_client = http_client
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    async def complete_with_tools(
        self,
        *,
        provider: ProviderConfig,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> ChatResponse:
        """Build the provider and run one chat turn (optionally with tools)."""

        effective_timeout = (
            self._timeout_seconds if self._timeout_seconds is not None else timeout_seconds
        )
        llm_provider = build_provider(
            provider,
            http_client=self._http_client,
            timeout_seconds=effective_timeout,
            max_retries=self._max_retries,
        )
        if system_prompt:
            payload_messages = [ChatMessage(role="system", content=system_prompt), *messages]
        else:
            payload_messages = list(messages)
        logger.info(
            "llm agent request",
            extra={
                "provider_type": provider.provider_type,
                "model": provider.model,
                "message_count": len(payload_messages),
                "tools": [tool.name for tool in tools or []],
            },
        )
        response = await llm_provider.chat(payload_messages, tools=tools)
        logger.info(
            "llm agent response",
            extra={
                "content_len": len(response.content),
                "tool_calls": [call.name for call in response.tool_calls],
            },
        )
        return response

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> str:
        """Plain completion without tools（filter 阶段使用）。"""

        response = await self.complete_with_tools(
            provider=provider,
            messages=[ChatMessage(role="user", content=prompt)],
            tools=None,
            timeout_seconds=timeout_seconds,
            system_prompt=system_prompt,
        )
        return response.content


@dataclass
class _ObservationBudget:
    """工具观察的总字符预算（达到后强制模型收口，过半时预警）。"""

    max_chars: int
    used: int = field(default=0)
    # 预警水位：used 首次越过 50% / 80% 时在下一条观察尾部注入提醒。
    warned_50: bool = field(default=False)
    warned_80: bool = field(default=False)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_chars

    def record(self, output: str) -> str:
        """记录一次观察长度（记录的是截断后的实际回填长度）。"""

        self.used += len(output)
        return output

    def warning_text(self) -> str | None:
        """返回本条观察应附带的预算预警（每档只提醒一次）。"""

        if self.exhausted:
            return None  # 耗尽走强制收口路径，不再重复提醒
        ratio = self.used / self.max_chars
        if ratio >= 0.8 and not self.warned_80:
            self.warned_80 = True
            return (
                f"\n[notice] investigation budget {self.used}/{self.max_chars} chars "
                "(80% used). Wrap up: only investigate what would change a finding, "
                "then output the final JSON."
            )
        if ratio >= 0.5 and not self.warned_50:
            self.warned_50 = True
            return (
                f"\n[notice] investigation budget {self.used}/{self.max_chars} chars "
                "(50% used). Prioritize remaining questions that would change a finding."
            )
        return None


@dataclass
class _RunState:
    """单次 review 的局部状态（并发安全：随 review 生命周期存在）。

    引擎实例是进程级单例（registry），同一实例可能被多个 review 并发调
    用——所有跨轮可变状态必须挂在这里而不是 ``self``，review 结束后随局部
    引用消失，GC 自动回收，不在实例上残留。
    """

    # (tool, canonical_args) -> (首次执行轮次, 回填观察) 的去重缓存。
    observation_cache: dict[tuple[str, str], tuple[int, str]] = field(
        default_factory=dict
    )
    # tool_call_id -> (工具名, 参数摘要)，供旧观察压缩时还原上下文。
    tool_call_meta: dict[str, tuple[str, str]] = field(default_factory=dict)
    # 连续「整轮工具调用全部命中去重缓存」的轮数（循环熔断计数器）。
    consecutive_cached_turns: int = 0


@register_engine
class LLMAgentEngine(ReviewEngine):
    """Agent-style diff review engine with read-only repo investigation tools."""

    _NAME = "llm-agent"

    def __init__(
        self,
        *,
        client: AgentLLMClient | None = None,
        timeout_seconds: float | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Create an engine instance.

        Args:
            client: Optional injectable agent client for tests/provider swaps.
            timeout_seconds: 每次 LLM 请求上限（秒）；``None`` 读 Settings。
            settings: Optional injected settings for tests；默认 ``get_settings()``。
        """

        self._settings = settings if settings is not None else get_settings()
        effective_timeout = (
            timeout_seconds if timeout_seconds is not None
            else self._settings.llm_request_timeout_seconds
        )
        if effective_timeout <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)
        self._client = client or ProviderAgentClient(
            max_retries=self._settings.llm_max_retries,
            timeout_seconds=effective_timeout,
        )
        self._timeout_seconds = effective_timeout
        # filter 阶段复用 LLMDirectEngine 的实现：组合一个内部实例，共享
        # 同一 client（AgentLLMClient 兼容其 LLMCompletionClient 协议）与
        # settings，避免把 filter 逻辑复制一份造成行为漂移。
        self._filter_engine = LLMDirectEngine(
            client=self._client,
            settings=self._settings,
            timeout_seconds=effective_timeout,
        )
        self.skipped_files: list[SkippedFile] = []
        logger.debug(
            "LLM agent engine config: timeout=%.1fs, max_turns=%d, "
            "tool_result_max_chars=%d, total_context_max_chars=%d, "
            "tool_timeout=%.1fs, filter_enabled=%s",
            self._timeout_seconds,
            self._settings.agent_max_turns,
            self._settings.agent_tool_result_max_chars,
            self._settings.agent_total_context_max_chars,
            self._settings.agent_tool_timeout_seconds,
            self._settings.llm_filter_enabled,
        )

    def name(self) -> str:
        """Return the registry identifier."""

        return self._NAME

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        """Review ``ctx`` with an investigation loop and return findings."""

        self.skipped_files = []
        if not ctx.diff_hunks:
            return []
        if ctx.provider is None:
            logger.warning(
                "llm-agent: no provider configured, returning no findings "
                "(review_id=%s)",
                ctx.review_id,
            )
            return []
        if ctx.repo_reader is None:
            logger.warning(
                "llm-agent: repo_reader missing from ReviewContext; the agent "
                "cannot investigate impact scope, returning no findings "
                "(review_id=%s). Check that the orchestrator injects "
                "repo_reader.",
                ctx.review_id,
            )
            return []

        started_at = time.perf_counter()
        tracer = build_tracer(ctx.review_id, self._settings)
        try:
            findings = await self._run_agent_loop(ctx, tracer)
        except NotImplementedError as exc:
            # custom 等不支持原生 tools 的 provider：明确降级而不是误报失败。
            await tracer.run_failed(reason="provider_without_tools", error=str(exc))
            logger.warning(
                "llm-agent: provider does not support native tool calling, "
                "degrading to empty findings (review_id=%s): %s",
                ctx.review_id,
                exc,
            )
            return []
        except LLMError as exc:
            await tracer.run_failed(reason="llm_error", error=str(exc))
            logger.warning(
                "llm-agent: LLM call failed, returning no findings "
                "(review_id=%s): %s",
                ctx.review_id,
                exc,
            )
            return []

        findings_before_filter = len(findings)
        if self._settings.llm_filter_enabled:
            findings = await self._filter_engine._filter_findings(ctx, findings)

        if not tracer.failed:
            await tracer.run_finished(
                findings_count=len(findings),
                filter_applied=self._settings.llm_filter_enabled,
                findings_before_filter=findings_before_filter,
            )

        logger.info(
            "llm-agent review finished: %d finding(s), review_id=%s, took %dms",
            len(findings),
            ctx.review_id,
            int((time.perf_counter() - started_at) * 1000),
        )
        return findings

    def supports_feedback(self) -> bool:
        """Return ``True``: history is injected into the prompt and filter."""

        return True

    async def health_check(self) -> HealthStatus:
        """Return lightweight health metadata without pinging upstream."""

        return HealthStatus(
            status="ok",
            details={
                "implementation": "llm-agent",
                "supports_feedback": True,
                "requires_repo_clone": False,
                "timeout_seconds": self._timeout_seconds,
                "max_turns": self._settings.agent_max_turns,
            },
            message=(
                "LLMAgentEngine is configured; provider health is checked "
                "during review calls."
            ),
        )

    # ---- agent loop ----

    async def _run_agent_loop(
        self,
        ctx: ReviewContext,
        tracer: AgentRunTracer,
    ) -> list[Finding]:
        """执行多轮工具调用循环，返回解析后的 findings。"""

        settings = self._settings
        assert ctx.provider is not None
        assert ctx.repo_reader is not None  # review() 已守卫缺失场景
        tools = build_default_tools(ctx.repo_reader)
        tools_by_name = {tool.name: tool for tool in tools}
        tool_specs = [ToolSpec(**tool_to_spec(tool)) for tool in tools]

        system_prompt = await self._build_system_prompt()
        messages: list[ChatMessage] = [
            ChatMessage(role="user", content=await self._build_user_prompt(ctx)),
        ]
        budget = _ObservationBudget(max_chars=settings.agent_total_context_max_chars)
        await tracer.run_started(
            provider_type=ctx.provider.provider_type,
            model=ctx.provider.model,
            max_turns=settings.agent_max_turns,
            tool_names=[tool.name for tool in tools],
            system_prompt_chars=len(system_prompt),
            user_prompt_chars=len(messages[0].content),
            budget_max_chars=budget.max_chars,
        )

        # review 局部状态：并发 review 互不共享，run 结束即无引用（GC 回收）。
        run_state = _RunState()
        final_text: str | None = None
        for turn in range(settings.agent_max_turns):
            call_started = time.perf_counter()
            response = await self._client.complete_with_tools(
                provider=ctx.provider,
                messages=messages,
                tools=tool_specs,
                timeout_seconds=self._timeout_seconds,
                system_prompt=system_prompt,
            )
            tracer.turns_used = turn + 1
            await tracer.llm_response(
                turn=turn,
                content=response.content,
                tool_calls=[
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in response.tool_calls
                ],
                usage=dict(response.usage),
                model=response.model,
                message_count=len(messages),
                duration_ms=int((time.perf_counter() - call_started) * 1000),
            )
            if not response.tool_calls:
                final_text = response.content
                tracer.final_text = final_text
                break

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                ),
            )
            observations = await self._execute_tool_calls(
                response.tool_calls,
                tools_by_name,
                budget,
                run_state,
                tracer,
                turn,
            )
            # 预算预警：过半/80% 时并入本批最后一条观察（一次性提醒）。
            warning = budget.warning_text()
            if warning and observations:
                observations[-1].content += warning
            # 循环熔断：连续 N 轮工具调用全部命中去重缓存 = 模型在原地打转。
            # 达到阈值注入纠正提示；达到 2 倍阈值强制收口（见下方 closeout）。
            stall_turns = settings.agent_stall_turns
            all_cached = bool(observations) and all(
                message.content.startswith("[cached]") for message in observations
            )
            if all_cached:
                run_state.consecutive_cached_turns += 1
            else:
                run_state.consecutive_cached_turns = 0
            if (
                stall_turns > 0
                and run_state.consecutive_cached_turns == stall_turns
                and observations
            ):
                observations[-1].content += _STALL_WARNING_TEXT.format(
                    turns=stall_turns,
                )
            # 旧观察压缩：把超过压缩轮数的 tool 观察替换为一行摘要，
            # 控制多轮循环的 prompt 膨胀（assistant 消息不动）。
            self._compress_old_observations(messages, turn, observations, run_state)
            messages.extend(observations)

            last_turn = turn == settings.agent_max_turns - 1
            stalled = (
                stall_turns > 0
                and run_state.consecutive_cached_turns >= stall_turns * 2
            )
            if last_turn or budget.exhausted or stalled:
                # 记录收口原因：预算耗尽优先于轮数耗尽；打转熔断兜底。
                if budget.exhausted:
                    tracer.closeout_reason = "budget_exhausted"
                elif stalled:
                    tracer.closeout_reason = "stalled_loop"
                else:
                    tracer.closeout_reason = "max_turns"
                # 收口：把强制收尾指令并进最后一条 tool message（保持
                # user/assistant 交替，兼容 Anthropic 的消息序列校验），
                # 然后不带 tools 再要一次最终 JSON。
                _append_force_final(observations[-1])
                call_started = time.perf_counter()
                response = await self._client.complete_with_tools(
                    provider=ctx.provider,
                    messages=messages,
                    tools=None,
                    timeout_seconds=self._timeout_seconds,
                    system_prompt=system_prompt,
                )
                await tracer.llm_response(
                    turn=turn,
                    content=response.content,
                    tool_calls=[],
                    usage=dict(response.usage),
                    model=response.model,
                    message_count=len(messages),
                    duration_ms=int((time.perf_counter() - call_started) * 1000),
                    is_closeout=True,
                )
                final_text = response.content
                tracer.final_text = final_text
                break

        if final_text is None:
            logger.warning(
                "llm-agent: loop ended without final JSON (review_id=%s)",
                ctx.review_id,
            )
            await tracer.run_failed(reason="no_final_json")
            return []

        try:
            return parse_findings_from_response(final_text, ctx)
        except ValueError as exc:
            logger.warning(
                "llm-agent: final response is not valid findings JSON, "
                "returning no findings (review_id=%s): %s",
                ctx.review_id,
                exc,
            )
            await tracer.run_failed(reason="invalid_final_json", error=str(exc))
            return []

    async def _execute_tool_calls(
        self,
        calls: list[ToolCall],
        tools_by_name: dict[str, Any],
        budget: _ObservationBudget,
        run_state: _RunState,
        tracer: AgentRunTracer,
        turn: int,
    ) -> list[ChatMessage]:
        """执行一批工具调用；失败/超时/非法参数都归一化为错误观察。

        同批调用并行执行（``asyncio.gather``）；重复的 ``(tool, args)`` 命中
        去重缓存，直接回填缓存观察且不重复计入预算。
        """

        max_chars = self._settings.agent_tool_result_max_chars

        # ---- 阶段 1：逐个解析/校验，划分「需执行」与「直接回填」两类 ----
        pending: list[tuple[int, ToolCall, Any, AgentTool]] = []
        direct: dict[int, tuple[str, str]] = {}
        for index, call in enumerate(calls):
            args = parse_tool_arguments(call.arguments)
            if args is None:
                direct[index] = (
                    (
                        f"[error] malformed arguments for {call.name} "
                        f"(must be a JSON object): {call.arguments[:200]}"
                    ),
                    STATUS_MALFORMED_ARGUMENTS,
                )
                continue
            tool = tools_by_name.get(call.name)
            if tool is None:
                available = ", ".join(sorted(tools_by_name))
                direct[index] = (
                    f"[error] unknown tool: {call.name} (available: {available})",
                    STATUS_UNKNOWN_TOOL,
                )
                continue
            if budget.exhausted:
                direct[index] = (
                    (
                        "[error] context budget exhausted; do not investigate "
                        "further and output the final findings JSON now"
                    ),
                    STATUS_BUDGET_EXHAUSTED,
                )
                continue
            cache_key = (call.name, _canonical_args(args))
            if cache_key in run_state.observation_cache:
                first_turn, first_output = run_state.observation_cache[cache_key]
                direct[index] = (
                    (
                        f"[cached] identical call already executed at turn {first_turn}; "
                        f"same result as before: {first_output[:200]}"
                    ),
                    STATUS_CACHED,
                )
                continue
            pending.append((index, call, args, tool))

        # ---- 阶段 2：需执行的调用并行跑（各自带超时与异常归一化） ----
        executed: dict[int, tuple[str, str, int | None]] = {}
        if pending:
            started = time.perf_counter()
            outcomes = await asyncio.gather(
                *(self._run_tool(call.name, tool, args) for _, call, args, tool in pending)
            )
            batch_ms = int((time.perf_counter() - started) * 1000)
            for (index, _call, _args, _tool), (output, status) in zip(
                pending, outcomes, strict=True
            ):
                executed[index] = (output, status, batch_ms)

        # ---- 阶段 3：按调用顺序组装观察（截断/记账/缓存/trace） ----
        results: list[ChatMessage] = []
        for index, call in enumerate(calls):
            if index in direct:
                output, status = direct[index]
                duration_ms = None
            else:
                output, status, duration_ms = executed[index]
                if budget.exhausted:
                    # 批内前序调用记账后预算已耗尽：本调用虽已并行执行，
                    # 观察仍替换为预算错误，保持「批内后续调用被拒」语义
                    # （与顺序执行时代的行为一致，模型收到明确停止信号）。
                    output = (
                        "[error] context budget exhausted; do not investigate "
                        "further and output the final findings JSON now"
                    )
                    status = STATUS_BUDGET_EXHAUSTED
                else:
                    # 先按回填口径截断/记账，trace 记录的是模型实际看到的观察内容。
                    clipped = self._clip(output, max_chars)
                    output = budget.record(clipped)
                    cache_key = (
                        call.name,
                        _canonical_args(parse_tool_arguments(call.arguments) or {}),
                    )
                    run_state.observation_cache[cache_key] = (turn, output)
            run_state.tool_call_meta[call.id] = (
                call.name,
                _canonical_args(parse_tool_arguments(call.arguments) or {}),
            )
            await tracer.tool_executed(
                turn=turn,
                call_id=call.id,
                name=call.name,
                raw_arguments=call.arguments,
                arguments=parse_tool_arguments(call.arguments),
                output=output,
                status=status,
                duration_ms=duration_ms,
                budget_used=budget.used,
                budget_max_chars=budget.max_chars,
            )
            results.append(
                ChatMessage(role="tool", tool_call_id=call.id, content=output),
            )
        return results

    def _compress_old_observations(
        self,
        messages: list[ChatMessage],
        current_turn: int,
        new_observations: list[ChatMessage],
        run_state: _RunState,
    ) -> None:
        """把超过压缩轮数的旧 tool 观察原地替换为一行摘要。

        压缩阈值取 ``agent_observation_compress_after_turns``（0 = 禁用）。
        只压缩 tool 消息；assistant / user 消息保持原样。摘要保留观察首行，
        让模型仍能引用早前的调查结论。本批新观察不压缩（刚产生）。
        """

        after_turns = self._settings.agent_observation_compress_after_turns
        if after_turns <= 0:
            return
        cutoff_turn = current_turn - after_turns
        if cutoff_turn < 0:
            return
        # 本批新观察（尚未 append 到 messages）不压缩。
        new_ids = {id(message) for message in new_observations}
        for message in messages:
            if id(message) in new_ids:
                continue
            if message.role != "tool" or message.content.startswith("[compressed]"):
                continue
            name, args_str = run_state.tool_call_meta.get(
                message.tool_call_id or "", ("tool", "")
            )
            message.content = _summarize_observation(message.content, name, args_str)

    async def _run_tool(
        self,
        name: str,
        tool: AgentTool,
        args: dict[str, Any],
    ) -> tuple[str, str]:
        """带超时执行单个工具；异常归一化为错误观察，返回 ``(观察, 状态)``。"""

        timeout = self._settings.agent_tool_timeout_seconds
        try:
            return await asyncio.wait_for(tool.execute(args), timeout=timeout), STATUS_OK
        except TimeoutError:  # noqa: UP041 - asyncio.wait_for 抛的是同一异常
            return f"[error] tool {name} timed out after {timeout:.0f}s", STATUS_TIMEOUT
        except Exception as exc:  # noqa: BLE001 - 工具失败必须以观察形式回填
            return f"[error] tool {name} failed: {exc}", STATUS_ERROR

    def _clip(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n... (truncated, total {len(text)} chars)"

    # ---- prompt building ----

    async def _build_system_prompt(self) -> str:
        """渲染 agent system prompt，注入全局审查原则（与 llm-direct 同源）。"""

        template = _load_prompt("agent_system.md")
        global_prompt = await _load_global_prompt()
        if global_prompt:
            return (
                "=== 全局审查原则 ===\n"
                f"{global_prompt}\n"
                "====================\n\n"
                f"{template}"
            )
        return template

    async def _build_user_prompt(self, ctx: ReviewContext) -> str:
        """渲染首轮 user prompt：MR 上下文 + 规则 + 负例 + diff（有界截断）。"""

        template = _load_prompt("agent_user.md")
        diff_block = LLMDirectEngine._format_diff(ctx.diff_hunks)
        fixed_values = {
            "mr_title": ctx.mr_title or "（无标题）",
            "mr_description": ctx.mr_description or "（无描述）",
            "last_commit_message": ctx.last_commit_message or "（无最新 commit message）",
            "source_branch": ctx.source_branch,
            "target_branch": ctx.target_branch,
            "source_commit_sha": ctx.source_commit_sha,
            "target_commit_sha": ctx.target_commit_sha,
            "rules_block": LLMDirectEngine._format_rules(ctx.rules),
            "history_block": await self._resolve_history_block(ctx),
        }
        rendered, truncated = LLMDirectEngine._truncate_diff(
            template=template,
            fixed_values=fixed_values,
            diff_block=diff_block,
            max_chars=self._settings.llm_prompt_max_chars,
            diff_key="diff_block",
        )
        if truncated:
            logger.warning(
                "llm-agent initial prompt exceeded max chars=%d, truncated diff "
                "from %d to %d chars (review_id=%s)",
                self._settings.llm_prompt_max_chars,
                len(diff_block),
                len(rendered),
                ctx.review_id,
            )
        values = {**fixed_values, "diff_block": rendered}
        return _render_template(template, values)

    async def _resolve_history_block(self, ctx: ReviewContext) -> str:
        """history_block：项目级负样本提示词优先，回退结构化历史。"""

        negative_prompt = await _load_negative_prompt(ctx.project_id)
        if negative_prompt:
            return negative_prompt
        return LLMDirectEngine._format_history(ctx.history)


def _canonical_args(args: dict[str, Any]) -> str:
    """把工具参数归一化为可比较的缓存 key（键排序 + 稳定序列化）。"""

    return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


def _summarize_observation(content: str, tool_name: str, args_str: str) -> str:
    """把一条旧 tool 观察压缩成一行摘要（保留工具名 + 参数 + 首行）。"""

    first_line = content.strip().splitlines()[0] if content.strip() else ""
    if len(first_line) > 120:
        first_line = first_line[:120] + "..."
    args_short = args_str[:80] + ("..." if len(args_str) > 80 else "")
    return f"[compressed] {tool_name}({args_short}) earlier observation: {first_line}"


def _append_force_final(message: ChatMessage) -> None:
    """把强制收尾指令并入最后一条 tool 观察消息（原地修改其 content）。"""

    message.content = message.content + _FORCE_FINAL_TEXT
