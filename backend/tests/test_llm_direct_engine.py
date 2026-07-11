"""Tests for the concrete llm-direct review engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from app.engines.llm_engine.engine import LLMDirectEngine, OpenAICompatibleLLMClient
from app.engines.types import (
    DiffHunk,
    ProviderConfig,
    ReviewContext,
    ReviewHistoryItem,
    RuleSpec,
)


@dataclass
class _FakeLLMClient:
    """Capture prompts and return queued responses for engine tests."""

    responses: list[str]
    prompts: list[str] = field(default_factory=list)

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
    ) -> str:
        _ = provider
        _ = timeout_seconds
        self.prompts.append(prompt)
        return self.responses.pop(0)


@dataclass
class _FakeProviderHTTPResponse:
    """HTTP response double used by provider-backed engine client tests."""

    payload: dict[str, Any]

    def json(self) -> dict[str, Any]:
        """Return queued JSON payload."""

        return self.payload

    def raise_for_status(self) -> None:
        """No-op for successful fake responses."""


@dataclass
class _FakeProviderHTTPClient:
    """Capture provider HTTP requests from OpenAICompatibleLLMClient."""

    responses: list[_FakeProviderHTTPResponse]
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeProviderHTTPResponse:
        """Record request and return the next fake response."""

        self.requests.append({"url": url, "headers": headers or {}, "json": json or {}})
        return self.responses.pop(0)


def _ctx(
    *,
    response_provider: bool = True,
    history: list[ReviewHistoryItem] | None = None,
) -> ReviewContext:
    provider = (
        ProviderConfig(
            provider_id=uuid4(),
            provider_type="openai-compatible",
            base_url="https://llm.example.com/v1",
            model="reviewer-1",
            api_key="test-key",
            temperature=0.0,
            max_tokens=2048,
        )
        if response_provider
        else None
    )
    return ReviewContext(
        review_id=uuid4(),
        project_id=uuid4(),
        mr_iid="42",
        source_branch="feature/login",
        target_branch="master",
        source_commit_sha="abc123",
        target_commit_sha="def456",
        diff_hunks=[
            DiffHunk(
                file_path="app/auth.py",
                old_path="app/auth.py",
                new_start=10,
                new_lines=6,
                old_start=10,
                old_lines=5,
                content="""@@ -10,5 +10,6 @@ def login(user):
 context = build_context(user)
+print(user.password)
+token = make_token(user)
 return token
""",
            )
        ],
        rules=[
            RuleSpec(
                id=uuid4(),
                rule_id="no-secret-logging",
                title="Do not log secrets",
                description="Passwords, tokens, and credentials must not be logged.",
                severity="BLOCKER",
                category="security",
                examples=["print(user.password)"],
            )
        ],
        provider=provider,
        history=history or [],
    )


@pytest.mark.asyncio
async def test_review_builds_five_section_prompt_and_parses_findings() -> None:
    """LLMDirectEngine sends the five-section prompt and parses JSON findings."""

    client = _FakeLLMClient(
        responses=[
            """
            {
              "findings": [
                {
                  "file_path": "app/auth.py",
                  "line_number": 11,
                  "rule_id": "no-secret-logging",
                  "severity": "BLOCKER",
                  "title": "Password is printed",
                  "description": "The new code prints user.password.",
                  "suggestion": "Remove the print or redact sensitive values.",
                  "existing_code": "print(user.password)",
                  "confidence": 0.95
                }
              ]
            }
            """
        ]
    )
    engine = LLMDirectEngine(client=client)

    findings = await engine.review(_ctx())

    assert len(findings) == 1
    finding = findings[0]
    assert finding.file_path == "app/auth.py"
    assert finding.line_number == 11
    assert finding.severity == "BLOCKER"
    assert finding.rule_id == "no-secret-logging"
    assert finding.confidence == 0.95

    prompt = client.prompts[0]
    assert "## 1. Review scope" in prompt
    assert "## 2. Active rules" in prompt
    assert "## 3. False-positive history" in prompt
    assert "## 4. Merge request diff" in prompt
    assert "## 5. Output contract" in prompt
    assert "no-secret-logging" in prompt
    assert "print(user.password)" in prompt


@pytest.mark.asyncio
async def test_review_resolves_missing_line_number_with_sliding_window() -> None:
    """When the LLM omits a line, existing_code is matched against added lines."""

    client = _FakeLLMClient(
        responses=[
            """
            {
              "findings": [
                {
                  "file_path": "app/auth.py",
                  "rule_id": "no-secret-logging",
                  "severity": "BLOCKER",
                  "title": "Password is printed",
                  "existing_code": "print(user.password)",
                  "confidence": 0.91
                }
              ]
            }
            """
        ]
    )
    engine = LLMDirectEngine(client=client)

    findings = await engine.review(_ctx())

    assert findings[0].line_number == 11


@pytest.mark.asyncio
async def test_review_filters_history_confirmed_false_positives() -> None:
    """Findings matching prior confirmed false positives are removed."""

    history = [
        ReviewHistoryItem(
            rule_id="no-secret-logging",
            file_path="app/auth.py",
            line_number=11,
            title="Password is printed",
            description="The new code prints user.password.",
            review_note="Allowed in this generated fixture.",
            confirmed_at="2026-06-30T00:00:00Z",
        )
    ]
    client = _FakeLLMClient(
        responses=[
            """
            {"findings": [{
              "file_path": "app/auth.py",
              "line_number": 11,
              "rule_id": "no-secret-logging",
              "severity": "BLOCKER",
              "title": "Password is printed",
              "description": "The new code prints user.password.",
              "existing_code": "print(user.password)",
              "confidence": 0.98
            }]}
            """
        ]
    )
    engine = LLMDirectEngine(client=client)

    findings = await engine.review(_ctx(history=history))

    assert findings == []


@pytest.mark.asyncio
async def test_review_returns_empty_when_provider_missing() -> None:
    """Without provider config the engine degrades safely to no findings."""

    client = _FakeLLMClient(responses=["should not be called"])
    engine = LLMDirectEngine(client=client)

    findings = await engine.review(_ctx(response_provider=False))

    assert findings == []
    assert client.prompts == []


@pytest.mark.asyncio
async def test_review_ignores_malformed_or_out_of_diff_findings() -> None:
    """Parser rejects malformed severities, unknown files, and out-of-range lines."""

    client = _FakeLLMClient(
        responses=[
            """
            ```json
            {"findings": [
              {
                "file_path": "app/auth.py",
                "line_number": 999,
                "rule_id": "x",
                "severity": "BLOCKER",
                "title": "outside"
              },
              {
                "file_path": "other.py",
                "line_number": 1,
                "rule_id": "x",
                "severity": "BLOCKER",
                "title": "wrong file"
              },
              {
                "file_path": "app/auth.py",
                "line_number": 11,
                "rule_id": "x",
                "severity": "CRITICAL",
                "title": "bad severity"
              },
              {
                "file_path": "app/auth.py",
                "line_number": 11,
                "rule_id": "no-secret-logging",
                "severity": "WARNING",
                "title": "ok",
                "confidence": 2
              }
            ]}
            ```
            """
        ]
    )
    engine = LLMDirectEngine(client=client)

    findings = await engine.review(_ctx())

    assert len(findings) == 1
    assert findings[0].title == "ok"
    assert findings[0].confidence == 1.0


@pytest.mark.asyncio
async def test_default_client_uses_provider_abstraction() -> None:
    """Default LLM client delegates OpenAI-compatible calls through app.llm providers."""

    http_client = _FakeProviderHTTPClient(
        responses=[
            _FakeProviderHTTPResponse(
                payload={
                    "choices": [{"message": {"content": "{\"findings\": []}"}}],
                    "usage": {"total_tokens": 12},
                }
            )
        ]
    )
    client = OpenAICompatibleLLMClient(http_client=http_client)

    provider_config = _ctx().provider
    assert provider_config is not None

    content = await client.complete(
        provider=provider_config,
        prompt="review this diff",
        timeout_seconds=5.0,
    )

    assert content == "{\"findings\": []}"
    request = http_client.requests[0]
    assert request["url"] == "https://llm.example.com/v1/chat/completions"
    assert request["json"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_health_check_reports_configured_provider() -> None:
    """Health status should reflect that the concrete implementation is active."""

    engine = LLMDirectEngine(client=_FakeLLMClient(responses=[]))

    status = await engine.health_check()

    assert status.status == "ok"
    assert status.details["implementation"] == "llm-direct"
    assert status.details["supports_feedback"] is True


@pytest.mark.asyncio
async def test_default_client_logs_request_and_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`OpenAICompatibleLLMClient.complete` 应输出 llm request/response 两条 INFO 日志。"""

    http_client = _FakeProviderHTTPClient(
        responses=[
            _FakeProviderHTTPResponse(
                payload={
                    "choices": [{"message": {"content": "{\"findings\": []}"}}],
                    "usage": {"total_tokens": 12},
                }
            )
        ]
    )
    client = OpenAICompatibleLLMClient(http_client=http_client)
    provider_config = _ctx().provider
    assert provider_config is not None

    with caplog.at_level(logging.INFO, logger="app.engines.llm_engine.engine"):
        raw = await client.complete(
            provider=provider_config,
            prompt="review this diff please",
            timeout_seconds=5.0,
        )

    assert raw == "{\"findings\": []}"

    messages = [record.getMessage() for record in caplog.records]
    assert "llm request" in messages
    assert "llm response" in messages

    request_record = next(r for r in caplog.records if r.getMessage() == "llm request")
    response_record = next(r for r in caplog.records if r.getMessage() == "llm response")
    assert request_record.prompt_len > 0  # type: ignore[attr-defined]
    assert response_record.response_len > 0  # type: ignore[attr-defined]
