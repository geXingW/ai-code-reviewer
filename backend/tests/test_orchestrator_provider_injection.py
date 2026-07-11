"""ReviewOrchestrator provider 注入路径单测。

覆盖四条分支：
1. Project 已关联 Provider → ReviewContext.provider 正确注入且 api_key 已解密；
2. Project 未在管理后台注册 → provider=None，引擎优雅退化不阻断；
3. Project 存在但 provider_id 为空 → provider=None；
4. session_factory=None（MVP 兼容路径）→ 直接 provider=None。

不经过 FastAPI，GitLab 客户端 mock，session_factory 用 test_engine 绑到当前 loop。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.engines import Finding as EngineFinding
from app.engines import ReviewContext
from app.engines.registry import EngineRegistry
from app.models.project import Project
from app.models.provider import Provider
from app.services.review_orchestrator import (
    GitLabMergeRequestEvent,
    ReviewOrchestrator,
)

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


class _CapturingEngine:
    """Stub engine that captures the ReviewContext for post-hoc assertions."""

    _NAME = "capture-engine"

    def __init__(self) -> None:
        self.captured: ReviewContext | None = None

    def name(self) -> str:
        return self._NAME

    async def review(self, context: ReviewContext) -> list[EngineFinding]:
        self.captured = context
        return []


def _make_registry(engine_impl: _CapturingEngine) -> EngineRegistry:
    registry = EngineRegistry()
    registry.register(engine_impl)  # type: ignore[arg-type]
    return registry


def _make_gitlab_mock() -> AsyncMock:
    client = AsyncMock()
    client.get_merge_request_changes.return_value = {
        "changes": [
            {
                "diff": "@@ -1,3 +1,4 @@\n line1\n+new line\n line2\n",
                "new_path": "app.py",
                "old_path": "app.py",
                "new_file": False,
                "deleted_file": False,
            }
        ],
        "diff_refs": {"base_sha": "b", "start_sha": "s", "head_sha": "h"},
    }
    client.create_merge_request_note.return_value = {"id": 100}
    client.set_commit_status.return_value = {"status": "success"}
    client.create_merge_request_discussion.return_value = {"id": "d1"}
    return client


def _make_event(gitlab_project_id: int = 999) -> GitLabMergeRequestEvent:
    return GitLabMergeRequestEvent(
        project_id=gitlab_project_id,
        project_path="group/repo",
        mr_iid=42,
        source_branch="feature/x",
        target_branch="master",
        source_commit_sha="abc123",
        target_commit_sha="def456",
        action="open",
        title="test MR",
        web_url="http://gitlab.example.com/mr/42",
    )


@pytest_asyncio.fixture
async def db_session_factory() -> AsyncGenerator[
    async_sessionmaker[AsyncSession], None
]:
    """Fresh schema per test; yields the async_sessionmaker."""

    test_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as connection:
        if TEST_DATABASE_URL.startswith("postgresql"):
            from sqlalchemy import text

            await connection.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    yield session_factory

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_provider_injected_when_project_linked(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Project 关联 Provider 时，ReviewContext.provider 应包含解密后的 api_key。"""

    session_factory = db_session_factory
    async with session_factory() as session:
        provider = Provider(
            name="ark-glm",
            protocol="openai_compatible",
            base_url="https://llm.example.com/v1",
            api_key="plaintext-secret",  # EncryptedString 会自动加密落库
            model="glm-4.6",
            temperature=0.2,
            max_tokens=8192,
        )
        session.add(provider)
        await session.flush()
        project = Project(
            name="linked-repo",
            gitlab_project_id="999",
            gitlab_access_token="tok",
            webhook_secret="sec",
            provider_id=provider.id,
        )
        session.add(project)
        await session.commit()

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=999))

    assert capturing.captured is not None
    provider_cfg = capturing.captured.provider
    assert provider_cfg is not None
    assert provider_cfg.provider_type == "openai_compatible"
    assert provider_cfg.base_url == "https://llm.example.com/v1"
    assert provider_cfg.model == "glm-4.6"
    # 关键：读出来时 EncryptedString 已解密回明文。
    assert provider_cfg.api_key == "plaintext-secret"
    assert provider_cfg.temperature == 0.2
    assert provider_cfg.max_tokens == 8192


@pytest.mark.asyncio
async def test_provider_none_when_project_not_registered(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Project 未在管理后台注册时，provider 保持 None，主流程仍返回 done。"""

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=db_session_factory,
    )
    result = await orch.review_merge_request(_make_event(gitlab_project_id=8888))

    assert result.status == "done"
    assert capturing.captured is not None
    assert capturing.captured.provider is None


@pytest.mark.asyncio
async def test_provider_none_when_project_has_no_provider(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Project 已注册但 provider_id 为空时，provider 应为 None。"""

    session_factory = db_session_factory
    async with session_factory() as session:
        project = Project(
            name="no-provider-repo",
            gitlab_project_id="555",
            gitlab_access_token="tok",
            webhook_secret="sec",
            provider_id=None,
        )
        session.add(project)
        await session.commit()

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=555))

    assert capturing.captured is not None
    assert capturing.captured.provider is None


@pytest.mark.asyncio
async def test_provider_none_when_session_factory_absent() -> None:
    """session_factory=None（MVP 兼容路径）时，直接 provider=None，不查库。"""

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=None,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=999))

    assert capturing.captured is not None
    assert capturing.captured.provider is None


@pytest.mark.asyncio
async def test_provider_none_when_provider_disabled(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Provider 存在但 enabled=False 时，provider 应为 None + warning。"""

    session_factory = db_session_factory
    async with session_factory() as session:
        provider = Provider(
            name="disabled-provider",
            protocol="openai_compatible",
            base_url="https://llm.example.com/v1",
            api_key="secret",
            model="glm-4.6",
            enabled=False,
        )
        session.add(provider)
        await session.flush()
        project = Project(
            name="disabled-provider-repo",
            gitlab_project_id="444",
            gitlab_access_token="tok",
            webhook_secret="sec",
            provider_id=provider.id,
        )
        session.add(project)
        await session.commit()

    capturing = _CapturingEngine()
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=_make_registry(capturing),
        default_engine="capture-engine",
        session_factory=session_factory,
    )
    await orch.review_merge_request(_make_event(gitlab_project_id=444))

    assert capturing.captured is not None
    assert capturing.captured.provider is None
