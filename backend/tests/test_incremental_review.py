"""增量审查（feat/incremental-review）单测：

覆盖 orchestrator 决策的六条主路径：
1. 首次审 MR → full；
2. 第二次 push（head 前进 + is_ancestor True）→ incremental，base=上次 head；
3. rebase / squash（is_ancestor False）→ history_rewritten 降级 full，parent 仍串；
4. 同 head 重复触发（CI 抖动）→ reuse，不新建 review、不调 engine；
5. 增量合并：旧 finding + 新 findings 匹配 → 保留 first_seen；旧文件被改而
   模型没再报同一位置 → status='resolved'；
6. 同 project 不同 mr_iid → 全新会话，不复用。

所有测试直接跑 ReviewOrchestrator + 真库（session_factory 绑定当前 event loop
的 async_sessionmaker），GitLab 客户端全部 mock，避免 HTTP / LLM 不确定性。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Sequence
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 依赖 EncryptedString 的模型需要 SECRET_KEY；测试进程启动时就固定一份 Fernet key，
# 与 tests/test_models.py 的做法一致，避免 project fixture 落库时炸。
os.environ.setdefault("SECRET_KEY", Fernet.generate_key().decode("utf-8"))

from app.core.config import get_settings  # noqa: E402
from app.core.db import Base  # noqa: E402
from app.engines import Finding as EngineFinding  # noqa: E402
from app.engines import ReviewContext  # noqa: E402
from app.engines.registry import EngineRegistry  # noqa: E402
from app.models.finding import Finding as FindingRow  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.review import Review as ReviewRow  # noqa: E402
from app.services.review_orchestrator import (  # noqa: E402
    GitLabMergeRequestEvent,
    ReviewOrchestrator,
)

# 缓存一次 settings 让 EncryptedString 拿到刚放进 env 的 SECRET_KEY。
get_settings.cache_clear()

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


class _StubEngine:
    """引擎替身：按 review 顺序依次返回预设 findings，不做真调。

    ``findings_sequence`` 允许一次测试内先后返回不同结果（模拟 push 前后 LLM
    观点差异），耗尽后返回空列表。
    """

    _NAME = "stub-engine"

    def __init__(self, findings_sequence: list[list[EngineFinding]]) -> None:
        self._sequence = list(findings_sequence)
        self.call_count = 0

    def name(self) -> str:
        return self._NAME

    async def review(self, context: ReviewContext) -> list[EngineFinding]:
        self.call_count += 1
        if not self._sequence:
            return []
        return list(self._sequence.pop(0))


def _registry(engine: _StubEngine) -> EngineRegistry:
    reg = EngineRegistry()
    reg.register(engine)  # type: ignore[arg-type]
    return reg


def _gitlab_mock(compare_response: dict | None = None) -> AsyncMock:
    """GitLabClient mock：默认 changes payload 有一个 app.py 变更；compare 可配。"""

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
    # compare_refs 默认返回空 dict → is_ancestor False；测试可以覆盖。
    client.compare_refs.return_value = compare_response if compare_response is not None else {}
    return client


PROJECT_GITLAB_ID = 4242


def _event(
    *,
    commit_sha: str = "head-a",
    mr_iid: int = 7,
    target_commit_sha: str = "base-master",
) -> GitLabMergeRequestEvent:
    return GitLabMergeRequestEvent(
        project_id=PROJECT_GITLAB_ID,
        project_path="group/inc",
        mr_iid=mr_iid,
        source_branch="feature/x",
        target_branch="master",
        source_commit_sha=commit_sha,
        target_commit_sha=target_commit_sha,
        action="update",
        title="incremental MR",
        web_url="http://gitlab.example.com/mr/7",
    )


def _finding(
    *, file_path: str = "app.py", line: int = 2, rule_id: str = "rule-a",
) -> EngineFinding:
    return EngineFinding(
        file_path=file_path,
        line_number=line,
        rule_id=rule_id,
        severity="WARNING",
        title="stub finding",
        description="d",
        confidence=0.5,
    )


@pytest_asyncio.fixture
async def session_factory_fixture() -> AsyncGenerator[
    async_sessionmaker[AsyncSession], None
]:
    """一次性建库 → 建表 → 交回 session_factory → 用完 drop_all。"""

    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        if TEST_DATABASE_URL.startswith("postgresql"):
            from sqlalchemy import text

            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed_project(factory: async_sessionmaker[AsyncSession]) -> Project:
    """给测试预置一条 Project（gitlab_project_id 与 event.project_id 对齐）。"""

    async with factory() as session:
        proj = Project(
            name="inc-repo",
            gitlab_project_id=str(PROJECT_GITLAB_ID),
            gitlab_access_token="t",
            webhook_secret="s",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        return proj


async def _list_reviews(
    factory: async_sessionmaker[AsyncSession],
) -> Sequence[ReviewRow]:
    async with factory() as session:
        result = await session.execute(
            select(ReviewRow).order_by(ReviewRow.created_at),
        )
        return list(result.scalars().all())


async def _list_findings(
    factory: async_sessionmaker[AsyncSession],
) -> Sequence[FindingRow]:
    async with factory() as session:
        result = await session.execute(
            select(FindingRow).order_by(FindingRow.created_at),
        )
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_first_review_uses_full_mode(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    """空 DB 首次 MR → full，base_sha == event.target_commit_sha，parent 为空。"""

    factory = session_factory_fixture
    await _seed_project(factory)

    engine = _StubEngine([[_finding()]])
    orch = ReviewOrchestrator(
        gitlab_client=_gitlab_mock(),
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    result = await orch.review_merge_request(_event(commit_sha="head-a"))

    assert result.status == "done"
    reviews = await _list_reviews(factory)
    assert len(reviews) == 1
    row = reviews[0]
    assert row.review_mode == "full"
    assert row.base_sha == "base-master"
    assert row.parent_review_id is None


@pytest.mark.asyncio
async def test_second_push_uses_incremental_mode(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    """head 前进 + compare.commits 非空 → incremental，base=上次 head，parent 串上。"""

    factory = session_factory_fixture
    await _seed_project(factory)

    # is_ancestor True：compare 返回一条 commits。
    gitlab = _gitlab_mock(compare_response={"commits": [{"id": "c1"}]})

    engine = _StubEngine(
        [
            [_finding(line=2, rule_id="rule-a")],  # 第一次
            [_finding(line=99, rule_id="rule-b")],  # 第二次 push 新问题
        ]
    )
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    await orch.review_merge_request(_event(commit_sha="head-a"))
    result = await orch.review_merge_request(_event(commit_sha="head-b"))

    assert result.status == "done"
    reviews = await _list_reviews(factory)
    assert len(reviews) == 2
    reviews_by_sha = {r.commit_sha: r for r in reviews}
    first, second = reviews_by_sha["head-a"], reviews_by_sha["head-b"]
    assert second.review_mode == "incremental"
    assert second.base_sha == "head-a"  # base 是上次的 head
    assert second.parent_review_id == first.id
    # incremental 走 compare 拉 diff（不再调 MR changes）。
    gitlab.compare_refs.assert_awaited()


@pytest.mark.asyncio
async def test_history_rewrite_falls_back_to_full(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    """is_ancestor False（rebase / squash）→ 降级 full，parent 仍串。"""

    factory = session_factory_fixture
    await _seed_project(factory)

    # compare.commits 为空 → is_ancestor False。
    gitlab = _gitlab_mock(compare_response={"commits": []})

    engine = _StubEngine([[_finding()], [_finding(line=8)]])
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    await orch.review_merge_request(_event(commit_sha="head-a"))
    await orch.review_merge_request(_event(commit_sha="head-b"))

    reviews = await _list_reviews(factory)
    assert len(reviews) == 2
    reviews_by_sha = {r.commit_sha: r for r in reviews}
    first, second = reviews_by_sha["head-a"], reviews_by_sha["head-b"]
    # 降级 full：base_sha 回到 event.target_commit_sha，parent 依然指向上一次。
    assert second.review_mode == "full"
    assert second.base_sha == "base-master"
    assert second.parent_review_id == first.id


@pytest.mark.asyncio
async def test_same_head_ci_retry_reuses_previous_review(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    """head 未变 → reuse：engine 只被调一次，DB 只 1 条 review，返回旧 review_id。"""

    factory = session_factory_fixture
    await _seed_project(factory)

    gitlab = _gitlab_mock()
    engine = _StubEngine([[_finding()]])
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    first = await orch.review_merge_request(_event(commit_sha="head-a"))
    second = await orch.review_merge_request(_event(commit_sha="head-a"))

    assert engine.call_count == 1
    reviews = await _list_reviews(factory)
    assert len(reviews) == 1
    assert first.review_id == second.review_id
    # note 应该被写两次（第一次正常 + 第二次 reuse 横幅）。
    assert gitlab.create_merge_request_note.await_count == 2
    reuse_body = gitlab.create_merge_request_note.await_args_list[1].kwargs["body"]
    assert "复用上一次审查结果" in reuse_body


@pytest.mark.asyncio
async def test_carried_over_findings_merged_and_marked(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    """
    情景：第一次审出两条 finding（app.py:2 / other.py:5），第二次 push 只让
    app.py 有 diff 且模型不再报 app.py:2 —— 期望：
      - app.py:2 → resolved（文件本次改过但没再报）；
      - other.py:5 → 保持 open（文件本次没动，历史遗留继续显示）；
      - 新报的 app.py:20 → 落成新 finding，first_seen 指向本次 review。
    """

    factory = session_factory_fixture
    await _seed_project(factory)

    gitlab = _gitlab_mock(compare_response={"commits": [{"id": "c1"}]})
    # 增量 diff 只涉及 app.py。
    gitlab.compare_refs.return_value = {
        "commits": [{"id": "c1"}],
        "diffs": [
            {
                "new_path": "app.py",
                "old_path": "app.py",
                "diff": "@@ -1,3 +1,4 @@\n line1\n+more\n line2\n",
                "new_file": False,
                "deleted_file": False,
            }
        ],
    }

    engine = _StubEngine(
        [
            [
                _finding(file_path="app.py", line=2, rule_id="rule-a"),
                _finding(file_path="other.py", line=5, rule_id="rule-b"),
            ],
            [
                _finding(file_path="app.py", line=20, rule_id="rule-c"),
            ],
        ]
    )
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    await orch.review_merge_request(_event(commit_sha="head-a"))
    result = await orch.review_merge_request(_event(commit_sha="head-b"))

    # note 显示合并后的总数：新增 1 + 历史遗留 1 = 2。
    assert result.finding_count == 2

    findings = await _list_findings(factory)
    # 3 条 finding：老 app.py:2 + 老 other.py:5 + 新 app.py:20。
    assert len(findings) == 3
    by_key = {(f.file_path, f.line_number, f.rule_id): f for f in findings}
    assert by_key[("app.py", 2, "rule-a")].status == "resolved"
    assert by_key[("app.py", 2, "rule-a")].resolved_in_review_id is not None
    assert by_key[("other.py", 5, "rule-b")].status == "open"
    # 新 finding 的 first_seen 指向第二次 review。
    # 用 commit_sha 定位而不是 index：MySQL DATETIME 秒级精度下两次调用
    # 可能落在同一秒，order by created_at 排序不稳定。
    reviews = await _list_reviews(factory)
    reviews_by_sha = {r.commit_sha: r for r in reviews}
    assert by_key[("app.py", 20, "rule-c")].first_seen_review_id == reviews_by_sha["head-b"].id
    assert by_key[("app.py", 20, "rule-c")].status == "open"


@pytest.mark.asyncio
async def test_mr_close_reopen_new_iid_full_review(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    """同 project、同 commit_sha、不同 mr_iid → 走全新 full 会话，不复用。"""

    factory = session_factory_fixture
    await _seed_project(factory)

    engine = _StubEngine([[_finding()], [_finding(line=9)]])
    gitlab = _gitlab_mock()
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    await orch.review_merge_request(_event(commit_sha="head-a", mr_iid=7))
    # 关闭后重开产生新 mr_iid，同 head 也应重头审。
    await orch.review_merge_request(_event(commit_sha="head-a", mr_iid=8))

    assert engine.call_count == 2
    reviews = await _list_reviews(factory)
    assert len(reviews) == 2
    assert {r.mr_iid for r in reviews} == {"7", "8"}
    assert all(r.review_mode == "full" for r in reviews)
    assert all(r.parent_review_id is None for r in reviews)
