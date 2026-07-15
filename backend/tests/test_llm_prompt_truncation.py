"""Tests for the ``_build_prompt`` diff-truncation overflow guard.

超大 diff 场景下 engine 必须把 diff_block 截到 ``llm_prompt_max_chars`` 以内，
避免直接把几十 K 字符发给 provider——那会撞 context window 上限、payload 上限，
或触发 provider 侧慢/贵路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.engines.llm_engine.engine import LLMDirectEngine
from app.engines.types import DiffHunk, ProviderConfig, ReviewContext


@dataclass
class _CapturingClient:
    """记录 complete() 收到的 prompt，便于对长度断言。"""

    responses: list[str]
    prompts: list[str] = field(default_factory=list)

    async def complete(
        self,
        *,
        provider: ProviderConfig,
        prompt: str,
        timeout_seconds: float,
        system_prompt: str | None = None,
    ) -> str:
        _ = provider, timeout_seconds, system_prompt
        self.prompts.append(prompt)
        return self.responses.pop(0)


def _ctx_with_diff(content: str) -> ReviewContext:
    return ReviewContext(
        review_id=uuid4(),
        project_id=uuid4(),
        mr_iid="1",
        source_branch="feature/x",
        target_branch="master",
        source_commit_sha="a" * 40,
        target_commit_sha="b" * 40,
        diff_hunks=[
            DiffHunk(
                file_path="app/big.py",
                old_path="app/big.py",
                new_start=1,
                new_lines=len(content.splitlines()),
                old_start=1,
                old_lines=1,
                content=content,
            )
        ],
        provider=ProviderConfig(
            provider_id=uuid4(),
            provider_type="openai-compatible",
            base_url="https://llm.example.com/v1",
            model="reviewer-1",
            api_key="test-key",
            temperature=0.0,
            max_tokens=2048,
        ),
        rules=[],
        mr_title="huge MR",
        mr_description="",
        last_commit_message="",
    )


@pytest.mark.asyncio
async def test_prompt_truncated_when_diff_exceeds_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """diff 撑爆预算时应被截到 max_chars 以内，并留下一条截断标记 + warning 日志。"""

    huge_diff = "@@ -1 +1 @@\n" + "+huge line of code\n" * 20_000  # 数十万字符
    settings = Settings(llm_filter_enabled=False, llm_prompt_max_chars=8_000)
    client = _CapturingClient(responses=['{"findings": []}'])
    engine = LLMDirectEngine(client=client, settings=settings)

    with caplog.at_level("WARNING", logger="app.engines.llm_engine.engine"):
        await engine.review(_ctx_with_diff(huge_diff))

    prompt = client.prompts[0]
    assert len(prompt) <= settings.llm_prompt_max_chars
    assert "diff truncated" in prompt
    # 至少要有一条 warning，明确写出预算和截断结果。
    assert any("prompt exceeded max chars" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_prompt_not_truncated_when_within_budget() -> None:
    """diff 在预算内时不动 prompt。"""

    small_diff = "@@ -1 +1 @@\n+small change\n"
    settings = Settings(llm_filter_enabled=False, llm_prompt_max_chars=32_000)
    client = _CapturingClient(responses=['{"findings": []}'])
    engine = LLMDirectEngine(client=client, settings=settings)

    await engine.review(_ctx_with_diff(small_diff))

    prompt = client.prompts[0]
    assert "diff truncated" not in prompt
    # 原始 diff 里的 "small change" 应完整保留
    assert "small change" in prompt
