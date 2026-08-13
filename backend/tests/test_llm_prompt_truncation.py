"""Tests for the ``_build_prompt`` diff-truncation overflow guard.

超大 diff 场景下 engine 必须把 diff_block 截到 ``llm_prompt_max_chars`` 以内，
避免直接把几十 K 字符发给 provider——那会撞 context window 上限、payload 上限，
或触发 provider 侧慢/贵路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from core.config import Settings
from engines.llm_engine.engine import LLMDirectEngine
from engines.types import DiffHunk, ProviderConfig, ReviewContext


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
async def test_large_diff_skipped_when_exceeds_file_max_chars(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """单文件 diff 超过 llm_file_max_chars 时直接跳过（reason=too_large），不调 LLM。

    按文件粒度并发审查后，超大文件不再截断后强行送审，而是整文件跳过、
    列入 skipped_files，避免把半截 diff 送给 LLM 导致审查质量不可控。
    """

    huge_diff = "@@ -1 +1 @@\n" + "+huge line of code\n" * 20_000  # 数十万字符
    settings = Settings(llm_filter_enabled=False, llm_file_max_chars=8_000)
    client = _CapturingClient(responses=['{"findings": []}'])
    engine = LLMDirectEngine(client=client, settings=settings)

    with caplog.at_level("INFO", logger="engines.llm_engine.engine"):
        findings = await engine.review(_ctx_with_diff(huge_diff))

    # 不调 LLM（大文件在信号量前就跳过了）
    assert len(client.prompts) == 0
    assert findings == []
    assert len(engine.skipped_files) == 1
    assert engine.skipped_files[0].file_path == "app/big.py"
    assert engine.skipped_files[0].reason == "too_large"
    # 有 info 级别日志说明跳过原因
    assert any("too_large" in rec.getMessage() for rec in caplog.records)


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
