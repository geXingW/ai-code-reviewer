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

from app.engines.base import ReviewEngine
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
_DEFAULT_TIMEOUT_SECONDS = 30.0
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)\s*```", re.DOTALL | re.IGNORECASE)
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


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


class LLMCompletionClient(Protocol):
    """Minimal async completion protocol used by ``LLMDirectEngine``."""

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
    ) -> str:
        """Return a raw text completion for ``prompt``."""


class OpenAICompatibleLLMClient:
    """Provider-backed completion client used by ``LLMDirectEngine`` by default."""

    def __init__(self, *, http_client: AsyncHTTPClient | None = None) -> None:
        """Create a completion client.

        Args:
            http_client: Optional provider HTTP transport for tests.
        """

        self._http_client = http_client

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
    ) -> str:
        """Call the configured provider through the shared LLM abstraction."""

        llm_provider = build_provider(provider, http_client=self._http_client)
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
                        content=_load_prompt("system.md"),
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
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Create an engine instance.

        Args:
            client: Optional injectable completion client for tests/provider swaps.
            timeout_seconds: Maximum seconds spent in one LLM request.
        """

        if timeout_seconds <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)
        self._client = client or OpenAICompatibleLLMClient()
        self._timeout_seconds = timeout_seconds

    def name(self) -> str:
        """Return the registry identifier."""

        return self._NAME

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        """Review ``ctx`` and return structured findings.

        The engine degrades safely to an empty list when provider configuration
        is absent, the upstream call fails, or the model returns malformed JSON.
        Returning no findings is preferable to breaking the webhook request path;
        operational details are logged server-side for later diagnosis.
        """

        if ctx.provider is None:
            logger.info("llm-direct review skipped: provider config missing")
            return []
        if not ctx.diff_hunks:
            logger.info("llm-direct review skipped: diff is empty")
            return []

        prompt = self._build_prompt(ctx)
        try:
            raw_response = await self._client.complete(
                provider=ctx.provider,
                prompt=prompt,
                timeout_seconds=self._timeout_seconds,
            )
            return self._parse_findings(raw_response, ctx)
        except (
            LLMError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            logger.exception("llm-direct review degraded to no findings: %s", exc)
            return []

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
        """

        values = {
            "mr_title": ctx.mr_title,
            "mr_description": ctx.mr_description or "（无描述）",
            "last_commit_message": ctx.last_commit_message or "（无最新 commit message）",
            "source_branch": ctx.source_branch,
            "target_branch": ctx.target_branch,
            "source_commit_sha": ctx.source_commit_sha,
            "target_commit_sha": ctx.target_commit_sha,
            "rules_block": self._format_rules(ctx.rules),
            "history_block": self._format_history(ctx.history),
            "diff_block": self._format_diff(ctx.diff_hunks),
        }
        return _render_template(_load_prompt("user.md"), values)

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
