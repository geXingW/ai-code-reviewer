"""ReviewOrchestrator 项目级引擎选择路径单测。

覆盖分支（解析函数级 + 编排器集成级）：
1. Project 未注册 / 未配置 engine_id → 使用 default_engine；
2. Project 配置 engine_id 且 engines.name 已注册 → 使用配置的引擎，
   ``reviews.engine_used`` 落库为实际引擎名；
3. Engine.enabled=False → 回退 default_engine + warning；
4. engines.name 未注册到 registry → 回退 default_engine + warning；
5. projects 查询异常（DB 故障）→ 回退 default_engine + error 日志；
6. session_factory=None（MVP 兼容路径）→ 直接 default_engine。

不经过 FastAPI，GitLab 客户端 mock，session_factory 用 test_engine 绑到当前 loop。
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core import config, db
from core.db import Base
from engines import Finding as EngineFinding
from engines import ReviewContext
from engines.registry import EngineRegistry
from models.engine import Engine
from models.project import Project
from models.review import Review
from repositories.project import ProjectRepository
from services.review_orchestration.resolution import _resolve_engine
from services.review_orchestrator import (
    GitLabMergeRequestEvent,
    GitLabPushEvent,
    ReviewOrchestrator,
)

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)

DEFAULT_ENGINE_NAME = "capture-engine"
PROJECT_ENGINE_NAME = "llm-agent"


class _StubEngine:
    """Stub engine recording how many times it was selected for review."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.review_calls = 0

    def name(self) -> str:
        return self._name

    async def review(self, context: ReviewContext) -> list[EngineFinding]:
        self.review_calls += 1
        return []


def _make_registry(*names: str) -> tuple[EngineRegistry, dict[str, _StubEngine]]:
    """Isolated registry with one stub engine per given name."""

    registry = EngineRegistry()
    stubs: dict[str, _StubEngine] = {}
    for name in names:
        stub = _StubEngine(name)
        stubs[name] = stub
        registry.register(stub)  # type: ignore[arg-type]
    return registry, stubs


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


async def _seed_engine(
    session_factory: async_sessionmaker[AsyncSession],
    name: str,
    *,
    enabled: bool = True,
) -> Engine:
    """插入一条 engines 配置并返回（脱管后属性仍可读）。"""

    async with session_factory() as session:
        engine = Engine(name=name, engine_type="builtin", enabled=enabled)
        session.add(engine)
        await session.commit()
        return engine


async def _seed_project(
    session_factory: async_sessionmaker[AsyncSession],
    gitlab_project_id: str = "999",
    *,
    engine: Engine | None = None,
    commit_review_enabled: bool = True,
) -> None:
    async with session_factory() as session:
        project = Project(
            name=f"repo-{gitlab_project_id}",
            gitlab_project_id=gitlab_project_id,
            gitlab_access_token="tok",
            webhook_secret="sec",
            engine_id=engine.id if engine is not None else None,
            # push / commit 链路会检查项目级开关（模型默认 False），默认开启
            # 让引擎选择测试能走完整管线。
            commit_review_enabled=commit_review_enabled,
        )
        session.add(project)
        await session.commit()


async def _fetch_last_engine_used(
    session_factory: async_sessionmaker[AsyncSession],
) -> str | None:
    async with session_factory() as session:
        result = await session.execute(
            select(Review.engine_used).order_by(Review.created_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()


@pytest_asyncio.fixture
async def db_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Fresh schema per test; yields the async_sessionmaker.

    Project 的 token / webhook_secret 是 EncryptedString 加密列，与
    conftest.db_session_factory 一致地注入临时 Fernet 密钥并清 settings 缓存。
    """

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode("utf-8"))
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()

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
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# _resolve_engine 解析函数级分支
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_engine_defaults_when_project_missing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Project 未在管理后台注册时，回退 default_engine。"""

    registry, _ = _make_registry(DEFAULT_ENGINE_NAME)
    resolved = await _resolve_engine(
        _make_event(gitlab_project_id=7777),
        session_factory=db_session_factory,
        registry=registry,
        default_engine=DEFAULT_ENGINE_NAME,
    )
    assert resolved == DEFAULT_ENGINE_NAME


@pytest.mark.asyncio
async def test_resolve_engine_defaults_when_engine_id_absent(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Project 已注册但未配置 engine_id 时，回退 default_engine。"""

    await _seed_project(db_session_factory, "999", engine=None)
    registry, _ = _make_registry(DEFAULT_ENGINE_NAME)
    resolved = await _resolve_engine(
        _make_event(gitlab_project_id=999),
        session_factory=db_session_factory,
        registry=registry,
        default_engine=DEFAULT_ENGINE_NAME,
    )
    assert resolved == DEFAULT_ENGINE_NAME


@pytest.mark.asyncio
async def test_resolve_engine_uses_configured_engine(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """engine_id 指向已启用且已注册的引擎时，返回该引擎名。"""

    engine = await _seed_engine(db_session_factory, PROJECT_ENGINE_NAME)
    await _seed_project(db_session_factory, "999", engine=engine)
    registry, _ = _make_registry(DEFAULT_ENGINE_NAME, PROJECT_ENGINE_NAME)
    resolved = await _resolve_engine(
        _make_event(gitlab_project_id=999),
        session_factory=db_session_factory,
        registry=registry,
        default_engine=DEFAULT_ENGINE_NAME,
    )
    assert resolved == PROJECT_ENGINE_NAME


@pytest.mark.asyncio
async def test_resolve_engine_falls_back_when_engine_disabled(
    db_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Engine.enabled=False 时回退 default_engine，并记 warning。"""

    engine = await _seed_engine(db_session_factory, PROJECT_ENGINE_NAME, enabled=False)
    await _seed_project(db_session_factory, "999", engine=engine)
    registry, _ = _make_registry(DEFAULT_ENGINE_NAME, PROJECT_ENGINE_NAME)
    with caplog.at_level(logging.WARNING):
        resolved = await _resolve_engine(
            _make_event(gitlab_project_id=999),
            session_factory=db_session_factory,
            registry=registry,
            default_engine=DEFAULT_ENGINE_NAME,
        )
    assert resolved == DEFAULT_ENGINE_NAME
    assert "project engine missing or disabled" in caplog.text


@pytest.mark.asyncio
async def test_resolve_engine_falls_back_when_name_not_registered(
    db_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """engines.name 未注册到 registry 时回退 default_engine，并记 warning。"""

    engine = await _seed_engine(db_session_factory, "no-such-engine")
    await _seed_project(db_session_factory, "999", engine=engine)
    registry, _ = _make_registry(DEFAULT_ENGINE_NAME)
    with caplog.at_level(logging.WARNING):
        resolved = await _resolve_engine(
            _make_event(gitlab_project_id=999),
            session_factory=db_session_factory,
            registry=registry,
            default_engine=DEFAULT_ENGINE_NAME,
        )
    assert resolved == DEFAULT_ENGINE_NAME
    assert "configured engine not registered" in caplog.text


@pytest.mark.asyncio
async def test_resolve_engine_falls_back_on_db_error(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """projects 表查询抛 SQLAlchemyError 时回退 default_engine，不向上抛。"""

    async def _boom(self: ProjectRepository, gitlab_project_id: str) -> Project:
        raise SQLAlchemyError("projects query failed")

    monkeypatch.setattr(ProjectRepository, "get_by_gitlab_project_id", _boom)
    registry, _ = _make_registry(DEFAULT_ENGINE_NAME)
    with caplog.at_level(logging.ERROR):
        resolved = await _resolve_engine(
            _make_event(gitlab_project_id=999),
            session_factory=db_session_factory,
            registry=registry,
            default_engine=DEFAULT_ENGINE_NAME,
        )
    assert resolved == DEFAULT_ENGINE_NAME
    assert "project engine resolution failed" in caplog.text


@pytest.mark.asyncio
async def test_resolve_engine_defaults_without_session_factory() -> None:
    """session_factory=None（MVP 兼容路径）时直接用 default_engine，不查库。"""

    registry, _ = _make_registry(DEFAULT_ENGINE_NAME)
    resolved = await _resolve_engine(
        _make_event(gitlab_project_id=999),
        session_factory=None,
        registry=registry,
        default_engine=DEFAULT_ENGINE_NAME,
    )
    assert resolved == DEFAULT_ENGINE_NAME


# ---------------------------------------------------------------------------
# ReviewOrchestrator 集成：engine 选择 + reviews.engine_used 落库
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_uses_project_engine_and_persists_it(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """项目配置 llm-agent 时：调 llm-agent 引擎，reviews.engine_used 落库为实际值。"""

    engine = await _seed_engine(db_session_factory, PROJECT_ENGINE_NAME)
    await _seed_project(db_session_factory, "999", engine=engine)
    registry, stubs = _make_registry(DEFAULT_ENGINE_NAME, PROJECT_ENGINE_NAME)
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=registry,
        default_engine=DEFAULT_ENGINE_NAME,
        session_factory=db_session_factory,
    )
    result = await orch.review_merge_request(_make_event(gitlab_project_id=999))

    assert result.status == "done"
    assert stubs[PROJECT_ENGINE_NAME].review_calls == 1
    assert stubs[DEFAULT_ENGINE_NAME].review_calls == 0
    assert await _fetch_last_engine_used(db_session_factory) == PROJECT_ENGINE_NAME


@pytest.mark.asyncio
async def test_orchestrator_defaults_when_project_has_no_engine(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """项目未配置引擎时行为与现状一致：走 default_engine 并落库 default_engine。"""

    await _seed_project(db_session_factory, "999", engine=None)
    registry, stubs = _make_registry(DEFAULT_ENGINE_NAME, PROJECT_ENGINE_NAME)
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=registry,
        default_engine=DEFAULT_ENGINE_NAME,
        session_factory=db_session_factory,
    )
    result = await orch.review_merge_request(_make_event(gitlab_project_id=999))

    assert result.status == "done"
    assert stubs[DEFAULT_ENGINE_NAME].review_calls == 1
    assert stubs[PROJECT_ENGINE_NAME].review_calls == 0
    assert await _fetch_last_engine_used(db_session_factory) == DEFAULT_ENGINE_NAME


@pytest.mark.asyncio
async def test_orchestrator_falls_back_when_configured_engine_unregistered(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """engine_id 指向未注册的引擎名时审查不报错，回退 default_engine 并落库。"""

    engine = await _seed_engine(db_session_factory, "no-such-engine")
    await _seed_project(db_session_factory, "999", engine=engine)
    registry, stubs = _make_registry(DEFAULT_ENGINE_NAME)
    orch = ReviewOrchestrator(
        gitlab_client=_make_gitlab_mock(),
        engine_registry=registry,
        default_engine=DEFAULT_ENGINE_NAME,
        session_factory=db_session_factory,
    )
    result = await orch.review_merge_request(_make_event(gitlab_project_id=999))

    assert result.status == "done"
    assert stubs[DEFAULT_ENGINE_NAME].review_calls == 1
    assert await _fetch_last_engine_used(db_session_factory) == DEFAULT_ENGINE_NAME


# ---------------------------------------------------------------------------
# Push Hook 合并审查集成：engine 选择（commit/push 共用 handle 管线）
# ---------------------------------------------------------------------------


def _make_push_gitlab_mock() -> AsyncMock:
    client = AsyncMock()
    client.compare_refs.return_value = {
        "diffs": [
            {
                "diff": "@@ -1,3 +1,4 @@\n line1\n+new line\n line2\n",
                "new_path": "app.py",
                "old_path": "app.py",
                "new_file": False,
                "deleted_file": False,
            }
        ],
        "commits": [],
    }
    client.create_commit_comment.return_value = {"id": 200}
    client.set_commit_status.return_value = {"status": "success"}
    return client


def _make_push_event(gitlab_project_id: int = 999) -> GitLabPushEvent:
    return GitLabPushEvent(
        project_id=gitlab_project_id,
        project_path="group/repo",
        # push 到 master 命中默认 block policy 模板，避免 skipped_no_policy。
        branch="master",
        before_sha="b" * 40,
        after_sha="a" * 40,
        commits=[{"id": "sha-1", "title": "feat: demo", "message": "feat: demo"}],
    )


@pytest.mark.asyncio
async def test_push_review_uses_project_engine(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """项目配置 llm-agent 时 push 合并审查同样走 llm-agent（与 MR 链路同源）。"""

    engine = await _seed_engine(db_session_factory, PROJECT_ENGINE_NAME)
    await _seed_project(db_session_factory, "999", engine=engine)
    registry, stubs = _make_registry(DEFAULT_ENGINE_NAME, PROJECT_ENGINE_NAME)
    orch = ReviewOrchestrator(
        gitlab_client=_make_push_gitlab_mock(),
        engine_registry=registry,
        default_engine=DEFAULT_ENGINE_NAME,
        session_factory=db_session_factory,
    )
    result = await orch.review_push(_make_push_event(gitlab_project_id=999))

    assert result.status == "done"
    assert stubs[PROJECT_ENGINE_NAME].review_calls == 1
    assert stubs[DEFAULT_ENGINE_NAME].review_calls == 0


@pytest.mark.asyncio
async def test_push_review_defaults_when_project_has_no_engine(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """项目未配置引擎时 push 审查走 default_engine，行为与改造前一致。"""

    await _seed_project(db_session_factory, "999", engine=None)
    registry, stubs = _make_registry(DEFAULT_ENGINE_NAME, PROJECT_ENGINE_NAME)
    orch = ReviewOrchestrator(
        gitlab_client=_make_push_gitlab_mock(),
        engine_registry=registry,
        default_engine=DEFAULT_ENGINE_NAME,
        session_factory=db_session_factory,
    )
    result = await orch.review_push(_make_push_event(gitlab_project_id=999))

    assert result.status == "done"
    assert stubs[DEFAULT_ENGINE_NAME].review_calls == 1
    assert stubs[PROJECT_ENGINE_NAME].review_calls == 0


@pytest.mark.asyncio
async def test_push_review_falls_back_when_configured_engine_unregistered(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """push 审查时 engine_id 指向未注册的引擎名 -> 回退 default_engine，不报错。"""

    engine = await _seed_engine(db_session_factory, "no-such-engine")
    await _seed_project(db_session_factory, "999", engine=engine)
    registry, stubs = _make_registry(DEFAULT_ENGINE_NAME)
    orch = ReviewOrchestrator(
        gitlab_client=_make_push_gitlab_mock(),
        engine_registry=registry,
        default_engine=DEFAULT_ENGINE_NAME,
        session_factory=db_session_factory,
    )
    result = await orch.review_push(_make_push_event(gitlab_project_id=999))

    assert result.status == "done"
    assert stubs[DEFAULT_ENGINE_NAME].review_calls == 1
