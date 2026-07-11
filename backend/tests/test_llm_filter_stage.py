"""Tests for the filter stage (LLM 证伪式后置过滤)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.engines.llm_engine.engine import LLMDirectEngine
from app.engines.llm_engine.filter_stage import (
    FilterDecision,
    apply_decisions,
    format_candidates,
    parse_filter_response,
)
from app.engines.types import (
    DiffHunk,
    Finding,
    ProviderConfig,
    ReviewContext,
    RuleSpec,
)


def _finding(
    *,
    file_path: str = "app/auth.py",
    line_number: int | None = 11,
    rule_id: str = "no-secret-logging",
    severity: str = "BLOCKER",
    title: str = "Password is printed",
    description: str | None = "The new code prints user.password.",
    suggestion: str | None = "Remove the print or redact sensitive values.",
    existing_code: str | None = "print(user.password)",
    confidence: float = 0.9,
) -> Finding:
    return Finding(
        file_path=file_path,
        line_number=line_number,
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        title=title,
        description=description,
        suggestion=suggestion,
        existing_code=existing_code,
        confidence=confidence,
    )


def _ctx() -> ReviewContext:
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
                content=(
                    "@@ -10,5 +10,6 @@ def login(user):\n"
                    " context = build_context(user)\n"
                    "+print(user.password)\n"
                    "+token = make_token(user)\n"
                    " return token\n"
                ),
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
        provider=ProviderConfig(
            provider_id=uuid4(),
            provider_type="openai-compatible",
            base_url="https://llm.example.com/v1",
            model="reviewer-1",
            api_key="test-key",
        ),
        history=[],
        mr_title="fix login",
        mr_description="removes password print",
        last_commit_message="fix: redact",
    )


@dataclass
class _FilterFakeClient:
    """记录 filter 阶段调用，支持给每次调用 queue 一个响应或抛错。"""

    responses: list[str | Exception]
    calls: list[dict[str, object]] = field(default_factory=list)

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> str:
        _ = provider, timeout_seconds
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


# --- 纯函数测试 --------------------------------------------------------------


def test_format_candidates_renders_all_fields() -> None:
    """每条 finding 的关键字段都应出现在渲染文本中。"""

    findings = [
        _finding(),
        _finding(
            file_path="app/service.py",
            line_number=42,
            rule_id="stale-todo",
            severity="INFO",
            title="TODO left behind",
            description="Old TODO comment.",
            suggestion="Resolve or remove.",
            existing_code="# TODO: fix",
        ),
    ]

    rendered = format_candidates(findings)

    assert "[0]" in rendered
    assert "[1]" in rendered
    assert "rule_id=no-secret-logging" in rendered
    assert "severity=BLOCKER" in rendered
    assert "file=app/auth.py:11" in rendered
    assert "title: Password is printed" in rendered
    assert "description: The new code prints user.password." in rendered
    assert "existing_code: print(user.password)" in rendered
    assert "suggestion: Remove the print or redact sensitive values." in rendered
    # 第二条
    assert "file=app/service.py:42" in rendered
    assert "title: TODO left behind" in rendered


def test_format_candidates_empty_returns_placeholder() -> None:
    """空 findings 返回占位文案，避免 LLM 拿到空段落误理解。"""

    assert format_candidates([]) == "（无候选 finding）"


# --- apply_decisions -------------------------------------------------------


def test_apply_decisions_keep_all_by_default() -> None:
    """空 decisions → 全部保留（含顺序）。"""

    findings = [_finding(title="A"), _finding(title="B"), _finding(title="C")]
    kept = apply_decisions(findings, [])
    assert [f.title for f in kept] == ["A", "B", "C"]


def test_apply_decisions_drops_flagged() -> None:
    """verdict=drop 的条目被移除，其它保留。"""

    findings = [_finding(title="A"), _finding(title="B"), _finding(title="C")]
    decisions = [
        FilterDecision(index=1, verdict="drop", reason="hallucination", new_severity=None),
    ]
    kept = apply_decisions(findings, decisions)
    assert [f.title for f in kept] == ["A", "C"]


def test_apply_decisions_downgrades_severity() -> None:
    """verdict=downgrade 的条目 severity 被替换，其它字段保持不变。"""

    findings = [_finding(title="A", severity="BLOCKER")]
    decisions = [
        FilterDecision(
            index=0,
            verdict="downgrade",
            reason="not blocking",
            new_severity="INFO",
        ),
    ]
    kept = apply_decisions(findings, decisions)
    assert len(kept) == 1
    assert kept[0].severity == "INFO"
    # 其它字段保持不变
    assert kept[0].title == "A"
    assert kept[0].rule_id == "no-secret-logging"


def test_apply_decisions_out_of_range_index_ignored() -> None:
    """非法 index 不导致崩溃，也不影响其它 finding。"""

    findings = [_finding(title="A"), _finding(title="B")]
    decisions = [
        FilterDecision(index=99, verdict="drop", reason="oob", new_severity=None),
        FilterDecision(index=-1, verdict="drop", reason="neg", new_severity=None),
    ]
    kept = apply_decisions(findings, decisions)
    assert [f.title for f in kept] == ["A", "B"]


def test_apply_decisions_preserves_order() -> None:
    """无论 decisions 顺序如何，keep 的顺序始终跟输入 findings 一致。"""

    findings = [_finding(title=str(i)) for i in range(5)]
    decisions = [
        FilterDecision(index=3, verdict="drop", reason="", new_severity=None),
        FilterDecision(index=0, verdict="drop", reason="", new_severity=None),
    ]
    kept = apply_decisions(findings, decisions)
    assert [f.title for f in kept] == ["1", "2", "4"]


# --- parse_filter_response -------------------------------------------------


def test_parse_filter_response_invalid_json_returns_empty() -> None:
    """无效 JSON → 空 decisions（外层 fail-open）。"""

    assert parse_filter_response("not json at all", 3) == []
    assert parse_filter_response("", 3) == []
    # 顶层不是 object
    assert parse_filter_response("[1, 2]", 3) == []


def test_parse_filter_response_ignores_bad_verdict() -> None:
    """verdict 不在白名单的条目被跳过。"""

    raw = """
    {"decisions": [
      {"index": 0, "verdict": "whatever", "reason": "?"},
      {"index": 1, "verdict": "drop", "reason": "ok"}
    ]}
    """
    decisions = parse_filter_response(raw, 3)
    assert len(decisions) == 1
    assert decisions[0].index == 1
    assert decisions[0].verdict == "drop"


def test_parse_filter_response_rejects_downgrade_without_severity() -> None:
    """downgrade 缺 new_severity 或非法枚举 → 跳过该条。"""

    raw = """
    {"decisions": [
      {"index": 0, "verdict": "downgrade"},
      {"index": 1, "verdict": "downgrade", "new_severity": "OOPS"},
      {"index": 2, "verdict": "downgrade", "new_severity": "WARNING"}
    ]}
    """
    decisions = parse_filter_response(raw, 3)
    assert len(decisions) == 1
    assert decisions[0].index == 2
    assert decisions[0].new_severity == "WARNING"


def test_parse_filter_response_rejects_out_of_range_index() -> None:
    """index 越界不进 decisions。"""

    raw = '{"decisions": [{"index": 99, "verdict": "drop"}]}'
    assert parse_filter_response(raw, 3) == []


# --- 集成到 engine.review() ----------------------------------------------


@dataclass
class _DualResponseClient:
    """两次调用：第一次返回主 review，第二次返回 filter decisions。"""

    review_response: str
    filter_response: str
    calls: list[dict[str, object]] = field(default_factory=list)

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> str:
        _ = provider, timeout_seconds
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        # 首次没有 system_prompt override（走默认 review system.md），第二次带 filter system_prompt
        if len(self.calls) == 1:
            return self.review_response
        return self.filter_response


@pytest.mark.asyncio
async def test_engine_review_calls_filter_when_enabled() -> None:
    """settings.llm_filter_enabled=True 时，engine 走第二次 LLM 调用并应用 decisions。"""

    review = """
    {"findings": [
      {"file_path": "app/auth.py", "line_number": 11, "rule_id": "no-secret-logging",
       "severity": "BLOCKER", "title": "A", "confidence": 0.9},
      {"file_path": "app/auth.py", "line_number": 11, "rule_id": "no-secret-logging",
       "severity": "BLOCKER", "title": "B", "confidence": 0.9},
      {"file_path": "app/auth.py", "line_number": 11, "rule_id": "no-secret-logging",
       "severity": "BLOCKER", "title": "C", "confidence": 0.9}
    ]}
    """
    filter_resp = """
    {"decisions": [
      {"index": 1, "verdict": "drop", "reason": "hallucinated"},
      {"index": 2, "verdict": "downgrade", "reason": "style only", "new_severity": "INFO"}
    ]}
    """
    client = _DualResponseClient(review_response=review, filter_response=filter_resp)
    engine = LLMDirectEngine(client=client, settings=Settings(llm_filter_enabled=True))

    findings = await engine.review(_ctx())

    assert len(findings) == 2
    assert findings[0].title == "A"
    assert findings[0].severity == "BLOCKER"
    assert findings[1].title == "C"
    assert findings[1].severity == "INFO"
    # 确认两次调用都发生了；第二次带 filter system_prompt
    assert len(client.calls) == 2
    assert client.calls[0]["system_prompt"] is None
    assert isinstance(client.calls[1]["system_prompt"], str)
    assert "adversarial" in client.calls[1]["system_prompt"].lower()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_engine_review_skips_filter_when_disabled() -> None:
    """settings.llm_filter_enabled=False 时，只调一次 LLM。"""

    review = """
    {"findings": [
      {"file_path": "app/auth.py", "line_number": 11, "rule_id": "no-secret-logging",
       "severity": "BLOCKER", "title": "A", "confidence": 0.9}
    ]}
    """
    client = _FilterFakeClient(responses=[review])
    engine = LLMDirectEngine(client=client, settings=Settings(llm_filter_enabled=False))

    findings = await engine.review(_ctx())

    assert len(findings) == 1
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_engine_review_filter_error_falls_back_to_original(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Filter LLM 抛异常 → 返回原始 findings + warning 日志（主流程不失败）。"""

    review = """
    {"findings": [
      {"file_path": "app/auth.py", "line_number": 11, "rule_id": "no-secret-logging",
       "severity": "BLOCKER", "title": "A", "confidence": 0.9}
    ]}
    """
    client = _FilterFakeClient(responses=[review, RuntimeError("filter blew up")])
    engine = LLMDirectEngine(client=client, settings=Settings(llm_filter_enabled=True))

    with caplog.at_level(logging.WARNING, logger="app.engines.llm_engine.engine"):
        findings = await engine.review(_ctx())

    assert len(findings) == 1
    assert findings[0].title == "A"
    assert len(client.calls) == 2  # 两次调用发生（第二次抛错）
    assert any("filter stage" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_engine_review_filter_invalid_json_falls_back_to_original() -> None:
    """Filter LLM 返回非 JSON → 原样保留 findings（parse 空 decisions）。"""

    review = """
    {"findings": [
      {"file_path": "app/auth.py", "line_number": 11, "rule_id": "no-secret-logging",
       "severity": "BLOCKER", "title": "A", "confidence": 0.9}
    ]}
    """
    client = _FilterFakeClient(responses=[review, "definitely not json"])
    engine = LLMDirectEngine(client=client, settings=Settings(llm_filter_enabled=True))

    findings = await engine.review(_ctx())

    assert len(findings) == 1
    assert findings[0].title == "A"


@pytest.mark.asyncio
async def test_engine_review_filter_empty_findings_skips_llm() -> None:
    """主 review 返回 0 findings 时，filter 阶段不再调 LLM。"""

    client = _FilterFakeClient(responses=['{"findings": []}'])
    engine = LLMDirectEngine(client=client, settings=Settings(llm_filter_enabled=True))

    findings = await engine.review(_ctx())

    assert findings == []
    # 仅一次调用（主 review），filter 阶段短路
    assert len(client.calls) == 1
