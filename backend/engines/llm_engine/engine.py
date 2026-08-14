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

import asyncio
import json
import logging
import re
import time
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import ValidationError

from core.config import Settings, get_settings
from engines.base import ReviewEngine
from engines.llm_engine.filter_stage import (
    FilterDecision,
    apply_decisions,
    format_candidates,
    format_filter_user_prompt,
    parse_filter_response,
    summarize_decisions,
)
from engines.llm_engine.language_detect import detect_languages
from engines.registry import register_engine
from engines.types import (
    DiffHunk,
    Finding,
    FindingSource,
    HealthStatus,
    ProviderConfig,
    ReviewContext,
    ReviewHistoryItem,
    RuleSpec,
    SkippedFile,
)
from llm import AsyncHTTPClient, ChatMessage, LLMError, build_provider

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


# ---- 全局提示词（带 TTL 缓存）----

_GLOBAL_PROMPT_KEY = "global_system_prompt"
_global_prompt_value: str = ""
_global_prompt_expires_at: float = 0.0
_GLOBAL_PROMPT_TTL_SECONDS = 60


async def _load_global_prompt() -> str:
    """从数据库读取全局 system prompt，带 60s 内存缓存。

    数据库未就绪或读取失败时返回空字符串，保证引擎可用性不受影响。
    空字符串表示不注入任何额外内容，行为与未配置时完全一致。
    """

    global _global_prompt_value, _global_prompt_expires_at

    now = time.monotonic()
    if now < _global_prompt_expires_at:
        return _global_prompt_value

    value = _global_prompt_value
    try:
        from sqlalchemy import select

        from core.db import AsyncSessionLocal
        from models.global_setting import GlobalSetting

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(GlobalSetting).where(GlobalSetting.key == _GLOBAL_PROMPT_KEY),
            )
            setting = result.scalar_one_or_none()
            value = setting.value if setting is not None else ""
    except Exception:  # noqa: BLE001 — DB 故障不应该阻塞审查
        logger.warning("failed to load global prompt from DB, using cached/empty value")
        # 失败时保持旧缓存值（如果有），但缩短 TTL 到 10 秒后重试
        _global_prompt_expires_at = now + 10
        return _global_prompt_value

    _global_prompt_value = value
    _global_prompt_expires_at = now + _GLOBAL_PROMPT_TTL_SECONDS
    return value


def _reset_global_prompt_cache() -> None:
    """Reset the global prompt cache (used by tests)."""

    global _global_prompt_value, _global_prompt_expires_at
    _global_prompt_value = ""
    _global_prompt_expires_at = 0.0


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
        base_system_prompt = (
            system_prompt if system_prompt is not None else _load_prompt("system.md")
        )
        # 主审查（使用默认 system.md）时，注入全局提示词到最前面。
        # filter 等专用 system prompt 不注入。
        if system_prompt is None:
            global_prompt = await _load_global_prompt()
            if global_prompt:
                effective_system_prompt = (
                    "=== 全局审查原则 ===\n"
                    f"{global_prompt}\n"
                    "====================\n\n"
                    f"{base_system_prompt}"
                )
            else:
                effective_system_prompt = base_system_prompt
        else:
            effective_system_prompt = base_system_prompt
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
        # 按文件粒度并发审查时记录被跳过的文件（过大 / 失败），由 orchestrator
        # 在 review() 返回后读取并交给 summary builder 渲染。每次 review() 重置。
        self.skipped_files: list[SkippedFile] = []
        # 启动/构造时打一次配置，便于线上排查"到底用了哪套 timeout/重试/filter 开关"。
        logger.debug(
            "LLM engine config: timeout=%.1fs, max_retries=%d, filter_enabled=%s, "
            "prompt_max_chars=%d, concurrency=%d, file_max_chars=%d",
            self._timeout_seconds,
            self._settings.llm_max_retries,
            self._settings.llm_filter_enabled,
            self._settings.llm_prompt_max_chars,
            self._settings.llm_concurrency,
            self._settings.llm_file_max_chars,
        )

    def name(self) -> str:
        """Return the registry identifier."""

        return self._NAME

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        """Review ``ctx`` and return structured findings.

        按文件粒度并发审查（feat/per-file-concurrent-review）：

        - 每个 diff hunk（单文件）独立调用一次 LLM，用 ``asyncio.Semaphore``
          按 ``llm_concurrency`` 控制并发度。
        - 单文件 diff 超过 ``llm_file_max_chars`` -> 整文件跳过（reason=too_large），
          不截断、不占并发槽。
        - 单文件 LLM 调用 / 响应解析失败 -> 降级为 skipped（reason=review_failed），
          不拖垮整体 review。``review()`` 本身不再因单文件失败而抛异常。
        - 跳过的文件记录在实例属性 ``self.skipped_files``，由 orchestrator 在
          review 完成后读取并交给 summary builder 渲染，避免用户误以为“全部审查通过”。
        - 全部文件的 findings 合并后统一走 ``_filter_findings``（filter 阶段
          逻辑不变，仍是单轮全量、fail-open）。

        Provider 缺失 / diff 为空仍安静降级为空列表。engine_error 仍只在
        ``review()`` 自身抛异常时触发：单文件失败已被内部降级，不会触发。
        """

        # 每次 review 重置 skipped_files，避免跨 review 串味。
        self.skipped_files = []

        if ctx.provider is None:
            logger.info("llm-direct review skipped: provider config missing")
            return []
        if not ctx.diff_hunks:
            logger.info("llm-direct review skipped: diff is empty")
            return []

        # 每次 review 打一条运行配置，方便对齐日志上"当前 review 走的是哪套 timeout"。
        logger.debug(
            "llm-direct review start: timeout=%.1fs, max_retries=%d, "
            "filter_enabled=%s, concurrency=%d, file_max_chars=%d, review_id=%s",
            self._timeout_seconds,
            self._settings.llm_max_retries,
            self._settings.llm_filter_enabled,
            self._settings.llm_concurrency,
            self._settings.llm_file_max_chars,
            ctx.review_id,
        )

        sem = asyncio.Semaphore(self._settings.llm_concurrency)
        tasks = [
            self._review_single_file(ctx, hunk, sem)
            for hunk in ctx.diff_hunks
        ]
        # _review_single_file 内部已 catch 所有单文件异常，gather 不会因单文件
        # 失败而抛异常；各文件的 findings 与 skipped 在 gather 完成后顺序汇总。
        results = await asyncio.gather(*tasks)

        all_findings: list[Finding] = []
        for findings, skipped in results:
            all_findings.extend(findings)
            if skipped is not None:
                self.skipped_files.append(skipped)

        logger.info(
            "llm-direct concurrent review: %d files reviewed, %d skipped "
            "(too_large=%d, review_failed=%d, filtered_out=%d, review_id=%s)",
            len(ctx.diff_hunks) - len(self.skipped_files),
            len(self.skipped_files),
            sum(1 for s in self.skipped_files if s.reason == "too_large"),
            sum(1 for s in self.skipped_files if s.reason == "review_failed"),
            sum(1 for s in self.skipped_files if s.reason == "filtered_out"),
            ctx.review_id,
        )

        return await self._filter_findings(ctx, all_findings)

    async def _review_single_file(
        self,
        ctx: ReviewContext,
        hunk: DiffHunk,
        sem: asyncio.Semaphore,
    ) -> tuple[list[Finding], SkippedFile | None]:
        """并发审查单个文件，返回 ``(findings, skipped)``。

        - 成功：``(findings_list, None)``
        - 跳过（diff 过大）：``([], SkippedFile(reason="too_large"))``，不占信号量
        - LLM 调用失败：``([], SkippedFile(reason="review_failed"))``
        - 响应解析失败：``([], SkippedFile(reason="review_failed"))``

        内部 catch 住所有 ``Exception``（LLMError 家族 + 解析异常 + 未预期异常），
        保证 ``asyncio.gather`` 不会因单文件失败而中断。``BaseException``
        （如 ``CancelledError``）不 catch，让其正常传播以支持取消。
        """

        max_chars = self._settings.llm_file_max_chars
        # review() 入口已校验 provider 非 None；这里 assert 给 mypy 做类型收窄。
        assert ctx.provider is not None, "ctx.provider must not be None at this point"
        # 先用单文件 diff 长度判是否超阈值；超阈值直接跳过，不占并发槽。
        single_diff = self._format_diff([hunk])
        if len(single_diff) > max_chars:
            logger.info(
                "llm-direct file skipped (too_large): %s (%d chars > %d, review_id=%s)",
                hunk.file_path,
                len(single_diff),
                max_chars,
                ctx.review_id,
            )
            return [], SkippedFile(
                file_path=hunk.file_path,
                reason="too_large",
                detail=f"{len(single_diff)} chars",
            )

        try:
            prompt = self._build_batch_prompt(
                ctx=ctx,
                batch_hunks=[hunk],
                batch_index=1,
                total_batches=1,
            )
            async with sem:
                raw_response = await self._client.complete(
                    provider=ctx.provider,
                    prompt=prompt,
                    timeout_seconds=self._timeout_seconds,
                )
            findings = self._parse_findings(raw_response, ctx)
        except LLMError as exc:
            logger.warning(
                "llm-direct file review_failed (llm call): %s (review_id=%s): %s",
                hunk.file_path,
                ctx.review_id,
                exc,
            )
            return [], SkippedFile(
                file_path=hunk.file_path,
                reason="review_failed",
                detail=f"LLM error: {exc}",
            )
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "llm-direct file review_failed (parse): %s (review_id=%s): %s",
                hunk.file_path,
                ctx.review_id,
                exc,
            )
            return [], SkippedFile(
                file_path=hunk.file_path,
                reason="review_failed",
                detail=f"response parsing failed: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - 防御兜底，保证单文件异常不拖垮 gather
            logger.exception(
                "llm-direct file review_failed (unexpected): %s (review_id=%s)",
                hunk.file_path,
                ctx.review_id,
            )
            return [], SkippedFile(
                file_path=hunk.file_path,
                reason="review_failed",
                detail=f"unexpected error: {exc}",
            )
        return findings, None

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

    def _build_batches(self, ctx: ReviewContext) -> list[list[DiffHunk]]:
        """把 diff_hunks 按文件贪心装箱，分成若干批次。

        .. deprecated:: feat/per-file-concurrent-review
            ``review()`` 已改为按文件粒度并发审查，不再调用本方法。保留实现
            仅供旧测试与潜在外部调用兼容，后续可删除。

        算法：
        - 按文件顺序逐个放入当前批次
        - 放入前估算「当前批次 + 该文件」的总 prompt 字符数是否超预算
        - 超预算 -> 关闭当前批次，开新批次
        - 单个文件就超预算 -> 独占一批（内部 diff 截断兜底）
        - 只有一批且装得下 -> 返回单元素列表，行为与改造前一致

        估算方式：直接渲染「固定段 + 当前批次 diff + 新文件 diff」的 prompt 长度，
        调用通用的 ``_truncate_diff`` 来判断是否需要截断（即是否超预算）。
        """

        max_chars = self._settings.llm_prompt_max_chars
        template = _load_prompt("user.md")
        languages = detect_languages(ctx.diff_hunks)
        base_fixed = self._base_fixed_values(ctx, languages)
        # 加上批次信息的额外开销（保守估算 200 字符）
        batch_info_overhead = 200
        effective_max = max_chars - batch_info_overhead
        if effective_max <= 0:
            effective_max = max_chars

        batches: list[list[DiffHunk]] = []
        current_batch: list[DiffHunk] = []
        current_diff_len = 0

        for hunk in ctx.diff_hunks:
            hunk_diff = self._format_diff([hunk])
            # 每个文件之间有 "\n\n" 分隔
            additional_len = len(hunk_diff) + (2 if current_batch else 0)

            if not current_batch:
                # 当前批次为空，直接放进去（哪怕单文件超预算也独占一批）
                current_batch.append(hunk)
                current_diff_len = len(hunk_diff)
                continue

            # 估算：固定段 + （当前 diff + 新增 diff） 是否超预算
            total_len = (
                self._prompt_fixed_len(template, base_fixed)
                + current_diff_len
                + additional_len
            )
            if total_len <= effective_max:
                current_batch.append(hunk)
                current_diff_len += additional_len
            else:
                # 当前批次满了，开新批次
                batches.append(current_batch)
                current_batch = [hunk]
                current_diff_len = len(hunk_diff)

        if current_batch:
            batches.append(current_batch)

        return batches

    def _build_batch_prompt(
        self,
        *,
        ctx: ReviewContext,
        batch_hunks: list[DiffHunk],
        batch_index: int,
        total_batches: int,
    ) -> str:
        """构建单批次的 user prompt。

        与 ``_build_prompt`` 的区别：
        - diff 只包含本批次文件
        - rules 只包含与本批次文件匹配的规则（按 path_patterns 过滤）
        - 加上批次信息（第 X/Y 批，本批文件列表）
        """

        languages = detect_languages(batch_hunks)
        max_chars = self._settings.llm_prompt_max_chars
        template = _load_prompt("user.md")
        diff_block = self._format_diff(batch_hunks)

        # 按本批次文件路径过滤规则
        batch_rules = self._rules_for_files(
            ctx.rules,
            [h.file_path for h in batch_hunks],
        )

        # 批次信息注入到 mr_description 字段的位置不合适，
        # 我们用一个额外的 "batch_info" 字段注入到 rules_block 之前
        # 简单起见：把批次信息拼在 diff_block 前面
        batch_info = (
            f"> **Batch {batch_index}/{total_batches}** - "
            f"reviewing {len(batch_hunks)} file(s): "
            f"{', '.join(h.file_path for h in batch_hunks[:5])}"
            f"{'...' if len(batch_hunks) > 5 else ''}\n"
        )

        base_fixed = self._base_fixed_values(ctx, languages)
        # 用过滤后的规则替换
        fixed_values = {**base_fixed, "rules_block": self._format_rules(batch_rules)}

        rendered_diff, truncated = self._truncate_diff(
            template=template,
            fixed_values=fixed_values,
            diff_block=batch_info + diff_block,
            max_chars=max_chars,
            diff_key="diff_block",
        )
        if truncated:
            logger.warning(
                "main batch %d/%d prompt exceeded max chars=%d, "
                "truncated diff from %d to %d chars (review_id=%s)",
                batch_index,
                total_batches,
                max_chars,
                len(diff_block),
                len(rendered_diff) - len(batch_info),
                ctx.review_id,
            )
        values = {**fixed_values, "diff_block": rendered_diff}
        return _render_template(template, values)

    @staticmethod
    def _rules_for_files(rules: list[RuleSpec], file_paths: list[str]) -> list[RuleSpec]:
        """按文件路径过滤规则，只保留与本批次文件相关的规则。

        - 规则 ``path_patterns`` 为空 -> 通用规则，始终保留
        - 规则有 ``path_patterns`` -> 只要有任意一个文件匹配任意一个 pattern 就保留
        - 匹配使用 fnmatch（支持 * ** ? 等 glob 模式）
        """

        if not rules:
            return []

        import fnmatch

        relevant: list[RuleSpec] = []
        for rule in rules:
            if not rule.enabled:
                continue
            # 没有路径限制的规则 = 通用规则，始终注入
            if not rule.path_patterns:
                relevant.append(rule)
                continue
            # 有路径限制的规则：只要匹配任一文件的任一 pattern 就保留
            matched = False
            for pattern in rule.path_patterns:
                for path in file_paths:
                    if fnmatch.fnmatch(path, pattern):
                        matched = True
                        break
                if matched:
                    break
            if matched:
                relevant.append(rule)
        return relevant

    def _base_fixed_values(
        self, ctx: ReviewContext, languages: list[str] | None = None
    ) -> dict[str, str]:
        """构建 prompt 固定段的 values 字典（不含 diff_block）。

        供分批逻辑复用，避免每批重复计算。languages 为 None 时从 ctx.diff_hunks 检测。
        """

        if languages is None:
            languages = detect_languages(ctx.diff_hunks)
        return {
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
        }

    @staticmethod
    def _prompt_fixed_len(template: str, fixed_values: dict[str, str]) -> int:
        """计算「空 diff」版本的 prompt 长度（固定段开销）。"""

        values_without_diff = {**fixed_values, "diff_block": ""}
        return len(_render_template(template, values_without_diff))

    def _build_prompt(self, ctx: ReviewContext) -> str:
        """构建单批 user prompt（向后兼容，内部委托给 _build_batch_prompt）。

        保留此方法是为了不破坏现有测试和外部调用。
        实际逻辑已迁移到分批版本。
        """

        return self._build_batch_prompt(
            ctx=ctx,
            batch_hunks=ctx.diff_hunks,
            batch_index=1,
            total_batches=1,
        )

    @staticmethod
    def _truncate_diff(
        *,
        template: str,
        fixed_values: dict[str, str],
        diff_block: str,
        max_chars: int,
        diff_key: str = "diff_block",
    ) -> tuple[str, bool]:
        """通用 diff 截断：把 diff_block 截到模板 + 固定变量内能容纳的长度。

        算法：先渲染出「空 diff」版本算固定开销 ``fixed``，允许给 diff 的预算是
        ``max_chars - fixed``。若 diff 已经在预算内直接返回；否则保留前
        ``budget - marker_len`` 个字符并在末尾追加截断标记。

        当固定开销自己就 >= max_chars 时，直接返回一个仅含截断标记的 diff--
        绝对不能返回负预算或空串再让下游猜。

        Args:
            template: prompt 模板字符串，含 ``{{key}}`` 占位符。
            fixed_values: 除 diff 外所有占位符的值字典。
            diff_block: 完整 diff 文本。
            max_chars: 整个 prompt 的最大字符数预算。
            diff_key: diff 在模板中的占位符 key，默认 ``diff_block``。

        Returns:
            ``(truncated_diff, was_truncated)`` 元组。
        """

        marker_template = "\n\n...(diff truncated: original %d chars, kept %d chars for length)"
        # 用 0-length marker 估算最大 marker 尺寸（避免 marker 自身让 budget 变负）。
        marker_reserve = len(marker_template % (10**9, 10**9))

        values_without_diff = {**fixed_values, diff_key: ""}
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
                        f"  category: {rule.category or 'other'}",
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
            parsed.append(_tag_finding_source(finding, ctx))
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
            # 模型偶尔会填 "null" 字符串或者空串--``_optional_str`` 只把空/None
            # 归成 None，其它字符串一律原样透传。合法性交给渲染层收敛。
            "category": _optional_str(raw.get("category")),
            "confidence": _clamp_confidence(raw.get("confidence")),
        }

    async def _filter_findings(
        self,
        ctx: ReviewContext,
        findings: list[Finding],
    ) -> list[Finding]:
        """对 findings 做证伪式后置过滤。

        Fail-open 契约：
        - 开关关闭 -> 原样返回，**不调用 LLM**。
        - findings 为空 -> 原样返回，**不调用 LLM**。
        - LLM 抛错 / 返回非 JSON / decisions 全部非法 -> warning 日志 + 原样返回。
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
        filter_template = _load_prompt("filter_user.md")
        max_chars = self._settings.llm_prompt_max_chars
        # Filter 阶段也要做 diff 截断，避免大 MR 时完整 diff 撑爆 context window。
        # 固定开销 = MR 上下文 + candidate findings 列表，diff 是可裁剪部分。
        filter_fixed_values = {
            "mr_title": ctx.mr_title or "（无标题）",
            "mr_description": ctx.mr_description or "（无描述）",
            "source_branch": ctx.source_branch,
            "target_branch": ctx.target_branch,
            "candidate_findings_block": candidate_block,
        }
        truncated_diff, filter_truncated = self._truncate_diff(
            template=filter_template,
            fixed_values=filter_fixed_values,
            diff_block=diff_block,
            max_chars=max_chars,
            diff_key="diff_block",
        )
        if filter_truncated:
            logger.warning(
                "filter prompt exceeded max chars=%d, truncated diff from %d to %d chars "
                "(review_id=%s, candidate_findings=%d)",
                max_chars,
                len(diff_block),
                len(truncated_diff),
                ctx.review_id,
                len(findings),
            )
        try:
            user_prompt = format_filter_user_prompt(
                template=filter_template,
                context=ctx,
                candidate_findings_block=candidate_block,
                diff_block=truncated_diff,
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

        kept_touched, dropped, downgraded, user_rule_kept, user_rule_blocked = (
            summarize_decisions(findings, decisions)
        )
        logger.info(
            "filter stage: kept %d, dropped %d, downgraded %d "
            "(explicit keep decisions: %d, user_rule_kept=%d, "
            "user_rule_drop_attempts_blocked=%d)",
            len(kept),
            dropped,
            downgraded,
            kept_touched,
            user_rule_kept,
            user_rule_blocked,
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


def _tag_finding_source(finding: Finding, ctx: ReviewContext) -> Finding:
    """按 ``finding.rule_id`` 是否命中 ``ctx.rules`` 打来源标签。

    命中启用中的团队/项目规则 -> ``USER_RULE``（Filter 默认保留）。

    其余一律 ``LLM_INFERRED``。语言 checklist 的内容里没有明确的
    rule_id 锚点，LLM 拿到 checklist 之后自己编 rule_id，无法反向回溯
    到具体 checklist--所以 ``FindingSource.LANGUAGE_CHECKLIST`` 目前
    保留占位、暂不使用（future work）。
    """

    user_rule_ids = {rule.rule_id for rule in ctx.rules if rule.enabled}
    if finding.rule_id in user_rule_ids:
        return finding.model_copy(update={"source": FindingSource.USER_RULE})
    return finding.model_copy(update={"source": FindingSource.LLM_INFERRED})


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
