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
from typing import Any, Protocol, cast

import httpx
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

logger = logging.getLogger(__name__)

_ALLOWED_SEVERITIES = {"INFO", "WARNING", "BLOCKER"}
_DEFAULT_TIMEOUT_SECONDS = 30.0
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)\s*```", re.DOTALL | re.IGNORECASE)


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
    """Small OpenAI-compatible chat completion client.

    The client posts to ``{provider.base_url}/chat/completions`` and extracts the
    first ``choices[0].message.content`` value. It deliberately does not own
    provider discovery, retry policy, or token resolution; those are expected to
    move into the dedicated provider abstraction later.
    """

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
    ) -> str:
        """Call an OpenAI-compatible chat-completions endpoint.

        Args:
            provider: Resolved provider settings, including plaintext API key.
            prompt: Prompt to send as a user message.
            timeout_seconds: HTTP timeout in seconds.

        Returns:
            str: Raw assistant message content.

        Raises:
            httpx.HTTPError: Network or non-2xx HTTP failures.
            ValueError: Response shape is missing expected content.
        """

        if not provider.base_url.strip():
            msg = "provider.base_url must not be empty"
            raise ValueError(msg)
        if not provider.model.strip():
            msg = "provider.model must not be empty"
            raise ValueError(msg)
        if not provider.api_key.strip():
            msg = "provider.api_key must not be empty"
            raise ValueError(msg)

        url = provider.base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": provider.model,
            "temperature": provider.temperature,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior code reviewer. Return only valid JSON "
                        "that follows the user's output contract."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        if provider.max_tokens is not None:
            payload["max_tokens"] = provider.max_tokens

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json=payload,
            )
            response.raise_for_status()
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            msg = "OpenAI-compatible response missing choices[0].message.content"
            raise ValueError(msg) from exc
        if not isinstance(content, str):
            msg = "OpenAI-compatible response content must be a string"
            raise ValueError(msg)
        return content


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
            httpx.HTTPError,
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
        """Build the five-section prompt required by Issue #6."""

        sections = [
            "## 1. Review scope\n" + self._format_scope(ctx),
            "## 2. Active rules\n" + self._format_rules(ctx.rules),
            "## 3. False-positive history\n" + self._format_history(ctx.history),
            "## 4. Merge request diff\n" + self._format_diff(ctx.diff_hunks),
            "## 5. Output contract\n" + self._format_output_contract(),
        ]
        return "\n\n".join(sections)

    @staticmethod
    def _format_scope(ctx: ReviewContext) -> str:
        return "\n".join(
            [
                f"MR IID: {ctx.mr_iid}",
                f"Source branch: {ctx.source_branch}",
                f"Target branch: {ctx.target_branch}",
                f"Source commit: {ctx.source_commit_sha}",
                f"Target commit: {ctx.target_commit_sha}",
                "Review only lines added or modified by this diff.",
                "Do not report style-only issues unless an active rule requires it.",
            ]
        )

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

    @staticmethod
    def _format_output_contract() -> str:
        return (
            "Return only JSON with this exact top-level shape:\n"
            "{\"findings\": [{\"file_path\": string, \"line_number\": number|null, "
            "\"rule_id\": string, \"severity\": \"INFO\"|\"WARNING\"|\"BLOCKER\", "
            "\"title\": string, \"description\": string|null, \"suggestion\": string|null, "
            "\"existing_code\": string|null, \"confidence\": number}]}\n"
            "Rules:\n"
            "- file_path must match a file in the diff.\n"
            "- line_number must refer to the new side of the diff.\n"
            "- If unsure, omit the finding.\n"
            "- Do not wrap the JSON in prose."
        )

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
