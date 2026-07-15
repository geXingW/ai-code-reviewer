"""Concrete diff-only LLM review engine.

``LLMDirectEngine`` reviews merge-request diffs by sending a structured
five-section prompt to an OpenAI-compatible chat-completions endpoint and
normalising the JSON response into runtime ``Finding`` objects.

The engine intentionally stays provider-light in this issue: it consumes the
existing ``ProviderConfig`` runtime object and exposes a small injectable client
protocol. A later provider abstraction can replace ``OpenAICompatibleLLMClient``
without changing the engine contract or tests.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.engines.base import ReviewEngine
from app.engines.llm_engine.filter_stage import (
    FilterDecision,
    apply_decisions,
    format_candidates,
    format_filter_user_prompt,
    parse_filter_response,
    summarize_decisions,
)
from app.engines.llm_engine.language_detect import detect_languages
from app.engines.registry import register_engine
from app.engines.types import (
    DiffHunk,
    Finding,
    HealthStatus,
    ProviderConfig,
    ReviewContext,
    ReviewHistoryItem,
    RuleSpec,
)
from app.llm import AsyncHTTPClient, ChatMessage, LLMError, build_provider

logger = logging.getLogger(__name__)

_ALLOWED_SEVERITIES = {"INFO", "WARNING", "BLOCKER"}
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)\s*```", re.DOTALL | re.IGNORECASE)
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_RULE_DOCS_DIR = Path(__file__).resolve().parent / "rule_docs"
_EMPTY_LANGUAGE_CHECKLIST = "No specific language checklists apply to this diff."


def _render_template(template: str, values: dict[str, str]) -> str:
    """简单的 {{key}} 占位符替换，不做转义、不支持条件分支。

    模板文件不来自用户输入，所以不做转义就足够；如果值里有 {{ 之类的
    字符也不做特殊处理（会原样出现在 prompt 里）。"""

    result = template
    for key, value in values.items():
        result = result.replace("{{" + key + "}}", value)
    return result


@cache
def _load_prompt(name: str) -> str:
    """从 prompts/ 目录读取指定 prompt 模板。lru_cache 避免每次 IO。"""

    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


@cache
def _load_rule_doc(language: str) -> str | None:
    """从 rule_docs/<language>.md 读 checklist；文件不存在返回 None。

    未来新增语言时若忘记补 md 文件，也只是跳过而不是抛异常。
    """

    path = _RULE_DOCS_DIR / f"{language}.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


class LLMCompletionClient(Protocol):
    """Minimal async completion protocol used by ``LLMDirectEngine``."""

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> str:
        """Return a raw text completion for ``prompt``.

        ``system_prompt=None`` 表示使用默认 review system prompt（``system.md``），
        filter 阶段传入 filter 专用 system prompt。
        """


class OpenAICompatibleLLMClient:
    """Provider-backed completion client used by ``LLMDirectEngine`` by default."""

    def __init__(
        self,
        *,
        http_client: AsyncHTTPClient | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        """Create a completion client.

        Args:
            http_client: Optional provider HTTP transport for tests.
            timeout_seconds: Optional per-request 超时，透传给 ``build_provider`` 让底
                层 httpx AsyncClient 使用。``None`` 走 provider 默认（30s）。
            max_retries: Optional 重试上限，透传给 ``LLMProvider`` 覆盖默认（2 次）。
        """

        self._http_client = http_client
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> str:
        """Call the configured provider through the shared LLM abstraction."""

        # 优先使用构造时注入的 timeout / 重试，参数 timeout_seconds 只在没注入时兜底。
        effective_timeout = (
            self._timeout_seconds if self._timeout_seconds is not None else timeout_seconds
        )
        llm_provider = build_provider(
            provider,
            http_client=self._http_client,
            timeout_seconds=effective_timeout,
            max_retries=self._max_retries,
        )
        effective_system_prompt = (
            system_prompt if system_prompt is not None else _load_prompt("system.md")
        )
        # 请求前记录关键元信息 + prompt 头部预览，避免刷屏；DEBUG 时打全量便于排查。
        logger.info(
            "llm request",
            extra={
                "provider_type": provider.provider_type,
                "model": provider.model,
                "prompt_len": len(prompt),
                "prompt_head": prompt[:500],
            },
        )
        logger.debug("llm request full prompt", extra={"prompt": prompt})
        try:
            response = await llm_provider.chat(
                [
                    ChatMessage(
                        role="system",
                        content=effective_system_prompt,
                    ),
                    ChatMessage(role="user", content=prompt),
                ]
            )
        except Exception as exc:
            # 记录失败元数据（不含 prompt 内容）后原样抛出，交由上层降级处理。
            logger.warning(
                "llm request failed",
                extra={
                    "provider_type": provider.provider_type,
                    "model": provider.model,
                    "error": str(exc),
                },
            )
            raise
        raw = response.content
        logger.info(
            "llm response",
            extra={
                "provider_type": provider.provider_type,
                "model": provider.model,
                "response_len": len(raw),
                "response_head": raw[:500],
            },
        )
        logger.debug("llm response full", extra={"response": raw})
        _ = timeout_seconds
        return raw


@register_engine
class LLMDirectEngine(ReviewEngine):
    """Diff-only LLM review engine."""

    _NAME = "llm-direct"

    def __init__(
        self,
        *,
        client: LLMCompletionClient | None = None,
        timeout_seconds: float | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Create an engine instance.

        Args:
            client: Optional injectable completion client for tests/provider swaps.
            timeout_seconds: 每次 LLM 请求上限（秒）。传 ``None`` 时从 ``Settings``
                的 ``llm_request_timeout_seconds`` 读取，允许 env 覆盖，也保留了显式
                传参入口用于测试。
            settings: Optional injected settings for tests；默认走 ``get_settings()``。
        """

        self._settings = settings if settings is not None else get_settings()
        effective_timeout = (
            timeout_seconds if timeout_seconds is not None
            else self._settings.llm_request_timeout_seconds
        )
        if effective_timeout <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)
        self._client = client or OpenAICompatibleLLMClient(
            max_retries=self._settings.llm_max_retries,
            timeout_seconds=effective_timeout,
        )
        self._timeout_seconds = effective_timeout
        # 启动/构造时打一次配置，便于线上排查"到底用了哪套 timeout/重试/filter 开关"。
        logger.debug(
            "LLM engine config: timeout=%.1fs, max_retries=%d, filter_enabled=%s, "
            "prompt_max_chars=%d",
            self._timeout_seconds,
            self._settings.llm_max_retries,
            self._settings.llm_filter_enabled,
            self._settings.llm_prompt_max_chars,
        )

    def name(self) -> str:
        """Return the registry identifier."""

        return self._NAME

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        """Review ``ctx`` and return structured findings.

        错误传播契约（Issue: fix/main-llm-fail-error-and-config）：

        - Provider 缺失 / diff 为空：安静降级为空列表——这些是可预期的"没什么要审
          的"场景，不能污染 orchestrator 的 engine_error 通道。
        - LLM 请求失败（TimeoutError / AuthError / ServerError / 其他 LLMError）：
          **直接抛出**，让 orchestrator 的 ``_handle_engine_error`` 落 status=
          engine_error、在 MR 上写"AI 审查失败"，绝不能把 timeout 装成 0 findings
          冒充 PASSED。
        - 响应解析失败（JSON 坏 / schema 错 / 值类型不对）：包装成 ``LLMError``
          再抛。在用户视角，"主 LLM 请求失败"与"主 LLM 输出无法解析"是同一件
          事——AI 审查未能给出可信结论。
        - Filter 阶段（``_filter_findings``）内部保留 fail-open：filter 挂了返回主
          审 findings 是刻意的降级策略。
        """

        if ctx.provider is None:
            logger.info("llm-direct review skipped: provider config missing")
            return []
        if not ctx.diff_hunks:
            logger.info("llm-direct review skipped: diff is empty")
            return []

        # 每次 review 打一条运行配置，方便对齐日志上"当前 review 走的是哪套 timeout"。
        logger.debug(
            "llm-direct review start: timeout=%.1fs, max_retries=%d, "
            "filter_enabled=%s, review_id=%s",
            self._timeout_seconds,
            self._settings.llm_max_retries,
            self._settings.llm_filter_enabled,
            ctx.review_id,
        )

        prompt = self._build_prompt(ctx)
        try:
            raw_response = await self._client.complete(
                provider=ctx.provider,
                prompt=prompt,
                timeout_seconds=self._timeout_seconds,
            )
        except LLMError:
            # LLMError 家族（TimeoutError / AuthError / ServerError / …）直接向
            # 上冒到 orchestrator，让 _handle_engine_error 走引擎失败分支。
            raise
        try:
            findings = self._parse_findings(raw_response, ctx)
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            # 解析异常升级为 LLMError：模型返回垃圾 JSON，视为审查失败而非"0 findings PASSED"。
            logger.error(
                "llm-direct: failed to parse LLM response: %s",
                exc,
                exc_info=True,
            )
            raise LLMError(f"LLM response parsing failed: {exc}") from exc

        return await self._filter_findings(ctx, findings)

    def supports_feedback(self) -> bool:
        """Return ``True`` because false-positive history is included in prompt/filtering."""

        return True

    async def health_check(self) -> HealthStatus:
        """Return lightweight health metadata without pinging the upstream provider."""

        return HealthStatus(
            status="ok",
            details={
                "implementation": "llm-direct",
                "supports_feedback": True,
                "requires_repo_clone": False,
                "timeout_seconds": self._timeout_seconds,
            },
            message=(
                "LLMDirectEngine is configured; provider health is checked during "
                "review calls."
            ),
        )

    def _build_prompt(self, ctx: ReviewContext) -> str:
        """构建 user prompt，system prompt 由 complete() 单独处理。

        历史上一个函数拼了两段（system + user），改造后：
        - system prompt 是纯静态文件（``system.md``）
        - user prompt 从 ``user.md`` 渲染，注入 diff / rules / history / MR 上下文

        对超大 diff 有一层软保护：如果按原样渲染会超过
        ``settings.llm_prompt_max_chars``，就把 ``diff_block`` 截断到能塞下，其余
        section（rules / checklist / history / MR context）**不动**——这些是本次
        审查的语义骨架，比某几行 diff 尾巴重要得多。
        """

        languages = detect_languages(ctx.diff_hunks)
        # 运营排查用：知道这次审查究竟叠加了哪些语言 checklist。
        logger.info(
            "LLM engine: detected languages=%s for review %s",
            languages,
            ctx.review_id,
        )
        max_chars = self._settings.llm_prompt_max_chars
        template = _load_prompt("user.md")
        diff_block = self._format_diff(ctx.diff_hunks)
        rendered_diff, truncated = self._maybe_truncate_diff(
            template=template,
            diff_block=diff_block,
            ctx=ctx,
            languages=languages,
            max_chars=max_chars,
        )
        if truncated:
            logger.warning(
                "prompt exceeded max chars=%d, truncated diff from %d to %d chars "
                "(review_id=%s)",
                max_chars,
                len(diff_block),
                len(rendered_diff),
                ctx.review_id,
            )
        values = {
            "mr_title": ctx.mr_title,
            "mr_description": ctx.mr_description or "（无描述）",
            "last_commit_message": ctx.last_commit_message or "（无最新 commit message）",
            "source_branch": ctx.source_branch,
            "target_branch": ctx.target_branch,
            "source_commit_sha": ctx.source_commit_sha,
            "target_commit_sha": ctx.target_commit_sha,
            "language_checklist_block": self._format_language_checklists(languages),
            "rules_block": self._format_rules(ctx.rules),
            "history_block": self._format_history(ctx.history),
            "diff_block": rendered_diff,
        }
        return _render_template(template, values)

    def _maybe_truncate_diff(
        self,
        *,
        template: str,
        diff_block: str,
        ctx: ReviewContext,
        languages: list[str],
        max_chars: int,
    ) -> tuple[str, bool]:
        """如果整个 prompt 超过 max_chars，截断 diff_block 到能塞下。

        算法：先渲染出"空 diff"版本算固定开销 ``fixed``，允许给 diff 的预算是
        ``max_chars - fixed``。若 diff 已经在预算内直接返回；否则保留前
        ``budget - marker_len`` 个字符并在末尾追加截断标记。

        当固定开销自己就 >= max_chars（rules / history / MR context 极大）时，直接
        返回一个仅含截断标记的 diff——绝对不能返回负预算或空串再让下游猜。
        """

        marker_template = "\n\n...(diff truncated: original %d chars, kept %d chars for length)"
        # 用 0-length marker 估算最大 marker 尺寸（避免 marker 自身让 budget 变负）。
        marker_reserve = len(marker_template % (10**9, 10**9))

        values_without_diff: dict[str, str] = {
            "mr_title": ctx.mr_title,
            "mr_description": ctx.mr_description or "（无描述）",
            "last_commit_message": ctx.last_commit_message or "（无最新 commit message）",
            "source_branch": ctx.source_branch,
            "target_branch": ctx.target_branch,
            "source_commit_sha": ctx.source_commit_sha,
            "target_commit_sha": ctx.target_commit_sha,
            "language_checklist_block": self._format_language_checklists(languages),
            "rules_block": self._format_rules(ctx.rules),
            "history_block": self._format_history(ctx.history),
            "diff_block": "",
        }
        fixed_prompt = _render_template(template, values_without_diff)
        fixed_len = len(fixed_prompt)

        if fixed_len + len(diff_block) <= max_chars:
            return diff_block, False

        budget = max_chars - fixed_len - marker_reserve
        if budget <= 0:
            # 固定段就已经超预算：给一个占位说明，不再塞 diff 内容。
            return marker_template % (len(diff_block), 0), True

        kept = diff_block[:budget]
        return kept + marker_template % (len(diff_block), len(kept)), True

    @staticmethod
    def _format_language_checklists(languages: list[str]) -> str:
        """把每种语言的 checklist 拼成 markdown 段落。

        - 空列表返回默认占位文案
        - 缺 md 文件的 language 直接跳过（不占位不报错）
        - 每种语言渲染成 ``### <Language> checklist`` 段落，段落之间空行分隔
        """

        if not languages:
            return _EMPTY_LANGUAGE_CHECKLIST
        blocks: list[str] = []
        for language in languages:
            body = _load_rule_doc(language)
            if body is None:
                continue
            blocks.append(f"### {language.capitalize()} checklist\n\n{body}")
        if not blocks:
            return _EMPTY_LANGUAGE_CHECKLIST
        return "\n\n".join(blocks)

    @staticmethod
    def _format_rules(rules: list[RuleSpec]) -> str:
        if not rules:
            return "No project-specific rules were supplied. Focus on correctness and security."
        blocks: list[str] = []
        for rule in rules:
            if not rule.enabled:
                continue
            examples = "; ".join(rule.examples) if rule.examples else "none"
            blocks.append(
                "\n".join(
                    [
                        f"- rule_id: {rule.rule_id}",
                        f"  title: {rule.title}",
                        f"  severity: {rule.severity}",
                        f"  category: {rule.category or 'general'}",
                        f"  description: {rule.description}",
                        f"  examples: {examples}",
                    ]
                )
            )
        return "\n".join(blocks) if blocks else "All supplied rules are disabled."

    @staticmethod
    def _format_history(history: list[ReviewHistoryItem]) -> str:
        if not history:
            return "No confirmed false-positive history is available."
        blocks: list[str] = []
        for item in history[:20]:
            blocks.append(
                "\n".join(
                    [
                        f"- rule_id: {item.rule_id}",
                        f"  file_path: {item.file_path}",
                        f"  line_number: {item.line_number}",
                        f"  title: {item.title}",
                        f"  description: {item.description or ''}",
                        f"  review_note: {item.review_note or ''}",
                    ]
                )
            )
        return "\n".join(blocks)

    @staticmethod
    def _format_diff(diff_hunks: list[DiffHunk]) -> str:
        blocks: list[str] = []
        for hunk in diff_hunks:
            blocks.append(
                "\n".join(
                    [
                        f"### File: {hunk.file_path}",
                        f"new_start={hunk.new_start}, new_lines={hunk.new_lines}",
                        "```diff",
                        hunk.content,
                        "```",
                    ]
                )
            )
        return "\n\n".join(blocks)

    def _parse_findings(self, raw_response: str, ctx: ReviewContext) -> list[Finding]:
        payload = _loads_model_json(raw_response)
        raw_findings = payload.get("findings", [])
        if not isinstance(raw_findings, list):
            return []

        parsed: list[Finding] = []
        for raw in raw_findings:
            if not isinstance(raw, Mapping):
                continue
            normalized = self._normalise_raw_finding(raw, ctx.diff_hunks)
            if normalized is None:
                continue
            try:
                finding = Finding(**normalized)
            except ValidationError:
                logger.info(
                    "llm-direct ignored invalid finding payload",
                    extra={"finding": normalized},
                )
                continue
            if _matches_false_positive_history(finding, ctx.history):
                continue
            parsed.append(finding)
        return parsed

    def _normalise_raw_finding(
        self,
        raw: Mapping[str, Any],
        diff_hunks: list[DiffHunk],
    ) -> dict[str, Any] | None:
        file_path = _optional_str(raw.get("file_path"))
        if file_path is None or not _file_in_diff(file_path, diff_hunks):
            return None

        severity = _optional_str(raw.get("severity"))
        if severity not in _ALLOWED_SEVERITIES:
            return None

        title = _optional_str(raw.get("title"))
        rule_id = _optional_str(raw.get("rule_id"))
        if not title or not rule_id:
            return None

        existing_code = _optional_str(raw.get("existing_code"))
        line_number = _optional_int(raw.get("line_number"))
        if line_number is None and existing_code:
            line_number = _resolve_line_number(file_path, existing_code, diff_hunks)
        if line_number is not None and not _line_in_diff(file_path, line_number, diff_hunks):
            return None

        return {
            "file_path": file_path,
            "line_number": line_number,
            "rule_id": rule_id,
            "severity": severity,
            "title": title,
            "description": _optional_str(raw.get("description")),
            "suggestion": _optional_str(raw.get("suggestion")),
            "existing_code": existing_code,
            "confidence": _clamp_confidence(raw.get("confidence")),
        }

    async def _filter_findings(
        self,
        ctx: ReviewContext,
        findings: list[Finding],
    ) -> list[Finding]:
        """对 findings 做证伪式后置过滤。

        Fail-open 契约：
        - 开关关闭 → 原样返回，**不调用 LLM**。
        - findings 为空 → 原样返回，**不调用 LLM**。
        - LLM 抛错 / 返回非 JSON / decisions 全部非法 → warning 日志 + 原样返回。
        - 只保留输入顺序中未被 drop 的 finding；downgrade 换新 severity。
        """

        settings = self._settings
        if not settings.llm_filter_enabled:
            logger.info(
                "filter stage: disabled by settings, returning %d findings unchanged",
                len(findings),
            )
            return findings
        if not findings:
            logger.info("filter stage: input 0 findings, skipping LLM call")
            return findings
        if ctx.provider is None:
            # 理论上到不了这里（review() 已早退），保险起见再兜一次。
            return findings

        candidate_block = format_candidates(findings)
        diff_block = self._format_diff(ctx.diff_hunks)
        try:
            user_prompt = format_filter_user_prompt(
                template=_load_prompt("filter_user.md"),
                context=ctx,
                candidate_findings_block=candidate_block,
                diff_block=diff_block,
            )
            system_prompt = _load_prompt("filter_system.md")
        except OSError as exc:
            logger.warning(
                "filter stage: failed to load prompt templates, falling back to original: %s",
                exc,
            )
            return findings

        logger.info("filter stage: input %d findings", len(findings))
        try:
            raw_response = await self._client.complete(
                provider=ctx.provider,
                prompt=user_prompt,
                timeout_seconds=self._timeout_seconds,
                system_prompt=system_prompt,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open：任何异常都不能拖累主流程
            logger.warning(
                "filter stage: LLM call failed, keeping original findings: %s",
                exc,
            )
            return findings

        decisions = parse_filter_response(raw_response, len(findings))
        if not decisions:
            # parse 空可能是 LLM 全 keep，也可能是格式非法；无论哪种都 fail-open。
            logger.info(
                "filter stage: no actionable decisions parsed, keeping all %d findings",
                len(findings),
            )
            return findings

        try:
            kept = apply_decisions(findings, decisions)
        except Exception as exc:  # noqa: BLE001 - 兜底防御
            logger.warning(
                "filter stage: apply_decisions raised, keeping original findings: %s",
                exc,
            )
            return findings

        kept_touched, dropped, downgraded = summarize_decisions(decisions)
        logger.info(
            "filter stage: kept %d, dropped %d, downgraded %d "
            "(explicit keep decisions: %d)",
            len(kept),
            dropped,
            downgraded,
            kept_touched,
        )
        logger.debug(
            "filter stage decisions",
            extra={"decisions": [_decision_to_dict(d) for d in decisions]},
        )
        return kept


def _loads_model_json(raw_response: str) -> dict[str, Any]:
    """Load model JSON, accepting optional fenced code blocks."""

    text = raw_response.strip()
    match = _JSON_BLOCK_RE.search(text)
    if match:
        text = match.group("body").strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        msg = "LLM response must be a JSON object"
        raise ValueError(msg)
    return cast(dict[str, Any], data)


def _decision_to_dict(decision: FilterDecision) -> dict[str, Any]:
    """把 FilterDecision 转成 dict 便于 DEBUG 日志序列化。"""

    return {
        "index": decision.index,
        "verdict": decision.verdict,
        "reason": decision.reason,
        "new_severity": decision.new_severity,
    }


def _file_in_diff(file_path: str, diff_hunks: list[DiffHunk]) -> bool:
    return any(hunk.file_path == file_path for hunk in diff_hunks)


def _line_in_diff(file_path: str, line_number: int, diff_hunks: list[DiffHunk]) -> bool:
    return any(
        line_number in _added_line_numbers(hunk)
        for hunk in diff_hunks
        if hunk.file_path == file_path
    )


def _resolve_line_number(
    file_path: str,
    existing_code: str,
    diff_hunks: list[DiffHunk],
) -> int | None:
    needle = " ".join(existing_code.strip().split())
    if not needle:
        return None
    for hunk in diff_hunks:
        if hunk.file_path != file_path:
            continue
        for line_no, code in _iter_added_lines(hunk):
            haystack = " ".join(code.strip().split())
            if needle in haystack or haystack in needle:
                return line_no
    return None


def _added_line_numbers(hunk: DiffHunk) -> set[int]:
    return {line_no for line_no, _ in _iter_added_lines(hunk)}


def _iter_added_lines(hunk: DiffHunk) -> list[tuple[int, str]]:
    """Return added lines with new-file line numbers for a unified diff hunk."""

    current_new_line = hunk.new_start
    added: list[tuple[int, str]] = []
    for raw_line in hunk.content.splitlines():
        if raw_line.startswith("@@"):
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added.append((current_new_line, raw_line[1:]))
            current_new_line += 1
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        current_new_line += 1
    return added


def _matches_false_positive_history(finding: Finding, history: list[ReviewHistoryItem]) -> bool:
    for item in history:
        if item.rule_id != finding.rule_id:
            continue
        if item.file_path != finding.file_path:
            continue
        if item.line_number is not None and finding.line_number is not None:
            if abs(item.line_number - finding.line_number) > 2:
                continue
        if item.title.strip().lower() == finding.title.strip().lower():
            return True
        if item.description and finding.description:
            if item.description.strip().lower() == finding.description.strip().lower():
                return True
    return False


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value).strip() or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if not isinstance(value, int | float | str | bytes | bytearray):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clamp_confidence(value: object) -> float:
    try:
        confidence = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))
