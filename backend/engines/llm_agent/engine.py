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
    """工具观察的总字符预算（达到后强制模型收口）。"""

    max_chars: int
    used: int = field(default=0)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_chars

    def record(self, output: str) -> str:
        """记录一次观察长度（记录的是截断后的实际回填长度）。"""

        self.used += len(output)
        return output


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
        try:
            findings = await self._run_agent_loop(ctx)
        except NotImplementedError as exc:
            # custom 等不支持原生 tools 的 provider：明确降级而不是误报失败。
            logger.warning(
                "llm-agent: provider does not support native tool calling, "
                "degrading to empty findings (review_id=%s): %s",
                ctx.review_id,
                exc,
            )
            return []
        except LLMError as exc:
            logger.warning(
                "llm-agent: LLM call failed, returning no findings "
                "(review_id=%s): %s",
                ctx.review_id,
                exc,
            )
            return []

        if self._settings.llm_filter_enabled:
            findings = await self._filter_engine._filter_findings(ctx, findings)

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

    async def _run_agent_loop(self, ctx: ReviewContext) -> list[Finding]:
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

        final_text: str | None = None
        for turn in range(settings.agent_max_turns):
            response = await self._client.complete_with_tools(
                provider=ctx.provider,
                messages=messages,
                tools=tool_specs,
                timeout_seconds=self._timeout_seconds,
                system_prompt=system_prompt,
            )
            if not response.tool_calls:
                final_text = response.content
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
            )
            messages.extend(observations)

            last_turn = turn == settings.agent_max_turns - 1
            if last_turn or budget.exhausted:
                # 收口：把强制收尾指令并进最后一条 tool message（保持
                # user/assistant 交替，兼容 Anthropic 的消息序列校验），
                # 然后不带 tools 再要一次最终 JSON。
                _append_force_final(observations[-1])
                response = await self._client.complete_with_tools(
                    provider=ctx.provider,
                    messages=messages,
                    tools=None,
                    timeout_seconds=self._timeout_seconds,
                    system_prompt=system_prompt,
                )
                final_text = response.content
                break

        if final_text is None:
            logger.warning(
                "llm-agent: loop ended without final JSON (review_id=%s)",
                ctx.review_id,
            )
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
            return []

    async def _execute_tool_calls(
        self,
        calls: list[ToolCall],
        tools_by_name: dict[str, Any],
        budget: _ObservationBudget,
    ) -> list[ChatMessage]:
        """执行一批工具调用；失败/超时/非法参数都归一化为错误观察。"""

        results: list[ChatMessage] = []
        max_chars = self._settings.agent_tool_result_max_chars
        for call in calls:
            args = parse_tool_arguments(call.arguments)
            if args is None:
                output = (
                    f"[error] malformed arguments for {call.name} "
                    f"(must be a JSON object): {call.arguments[:200]}"
                )
            else:
                tool = tools_by_name.get(call.name)
                if tool is None:
                    available = ", ".join(sorted(tools_by_name))
                    output = f"[error] unknown tool: {call.name} (available: {available})"
                elif budget.exhausted:
                    output = (
                        "[error] context budget exhausted; do not investigate "
                        "further and output the final findings JSON now"
                    )
                else:
                    output = await self._run_tool(call.name, tool, args)
            output = budget.record(self._clip(output, max_chars))
            results.append(
                ChatMessage(role="tool", tool_call_id=call.id, content=output),
            )
        return results

    async def _run_tool(self, name: str, tool: AgentTool, args: dict[str, Any]) -> str:
        """带超时执行单个工具；异常归一化为错误字符串。"""

        timeout = self._settings.agent_tool_timeout_seconds
        try:
            return await asyncio.wait_for(tool.execute(args), timeout=timeout)
        except TimeoutError:  # noqa: UP041 - asyncio.wait_for 抛的是同一异常
            return f"[error] tool {name} timed out after {timeout:.0f}s"
        except Exception as exc:  # noqa: BLE001 - 工具失败必须以观察形式回填
            return f"[error] tool {name} failed: {exc}"

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


def _append_force_final(message: ChatMessage) -> None:
    """把强制收尾指令并入最后一条 tool 观察消息（原地修改其 content）。"""

    message.content = message.content + _FORCE_FINAL_TEXT
