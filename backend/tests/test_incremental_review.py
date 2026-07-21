"""按"改动文件重刷"审查模式（feat/rescan-changed-files）的单测：

主线：新 push → 只审"本次 push 改动的文件"的 base..head 完整 diff；审完后
把这些文件的历史 finding + GitLab discussion 整体换代。未改动文件的老 finding
和 discussion 全部保留。

覆盖：
1. 首次审 MR → full；
2. 第二次 push（head 前进 + is_ancestor True）→ incremental，compare 返回的
   diffs 决定改动文件集合，changes 端点拿 base..head 完整 diff 后按改动集合
   过滤送引擎；
3. 只审改动文件：diff_hunks 中不出现未改动文件；
4. 改动文件的历史 discussion 被 resolve；老 finding DB 里 status='resolved'；
   未改动文件的老 finding 保持 open、不 resolve；
5. resolve API 抛异常 → warning，但 DB 层 status 仍标 resolved，主流程继续；
6. rebase / squash（is_ancestor False）→ history_rewritten 降级 full；
7. 同 head 重复触发 → reuse；
8. 同 project 不同 mr_iid → 全新 full 会话。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from typing import Any
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
        # 记录每次 engine.review 收到的 context 里 diff_hunks 文件集合，
        # 便于断言"只审改动文件"。
        self.received_file_sets: list[list[str]] = []

    def name(self) -> str:
        return self._NAME

    async def review(self, context: ReviewContext) -> list[EngineFinding]:
        self.call_count += 1
        self.received_file_sets.append([h.file_path for h in context.diff_hunks])
        if not self._sequence:
            return []
        return list(self._sequence.pop(0))


def _registry(engine: _StubEngine) -> EngineRegistry:
    reg = EngineRegistry()
    reg.register(engine)  # type: ignore[arg-type]
    return reg


def _mr_changes_payload(files: list[str]) -> dict:
    """构造一个覆盖 ``files`` 的 MR changes 响应；每个文件都有 non-trivial diff。"""

    changes = []
    for path in files:
        changes.append(
            {
                "diff": f"@@ -1,3 +1,4 @@\n line-a\n+new-{path}\n line-b\n",
                "new_path": path,
                "old_path": path,
                "new_file": False,
                "deleted_file": False,
            },
        )
    return {
        "changes": changes,
        "diff_refs": {"base_sha": "b", "start_sha": "s", "head_sha": "h"},
    }


def _gitlab_mock(
    *,
    compare_response: dict | None = None,
    changes_files: list[str] | None = None,
) -> AsyncMock:
    """GitLabClient mock：默认 changes 覆盖 app.py 一个文件；compare / changes 可配。

    - ``compare_response``: :meth:`compare_refs` 返回值；默认 ``{}`` → 视作非祖先
      关系（老测试路径行为）。传值时应符合真实 compare API 结构：``commits`` +
      ``diffs``（本 PR 用 diffs 提取改动文件集合）。
    - ``changes_files``: :meth:`get_merge_request_changes` 返回的文件列表；默认
      仅 ``app.py``。
    """

    client = AsyncMock()
    client.get_merge_request_changes.return_value = _mr_changes_payload(
        changes_files if changes_files is not None else ["app.py"],
    )
    client.create_merge_request_note.return_value = {"id": 100}
    client.set_commit_status.return_value = {"status": "success"}
    client.create_merge_request_discussion.side_effect = _make_discussion_side_effect()
    # resolve_discussion 默认 noop，测试可覆盖 side_effect 让它抛错。
    client.resolve_discussion.return_value = {"resolved": True}
    client.compare_refs.return_value = compare_response if compare_response is not None else {}
    return client


def _make_discussion_side_effect() -> Callable[..., Awaitable[dict[str, Any]]]:
    """按创建次序为每次 create_merge_request_discussion 分配确定性 discussion id。

    改用 side_effect 而不是 return_value：新 discussion 每次拿到不同的字符串 id，
    才能断言 orchestrator 把 id 正确回写到对应 Finding 行。
    """

    counter = {"n": 0}

    async def _create(**_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        counter["n"] += 1
        return {"id": f"disc-{counter['n']}"}

    return _create


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
    """head 前进 + compare 拿到改动文件集合 → incremental，base=上次 head，parent 串上。

    新语义：compare 用来判祖先 + 拿改动文件集合；实际 diff 走 MR changes（base..head）。
    """

    factory = session_factory_fixture
    await _seed_project(factory)

    # is_ancestor True + compare.diffs 里出现 app.py。
    gitlab = _gitlab_mock(
        compare_response={
            "commits": [{"id": "c1"}],
            "diffs": [{"new_path": "app.py", "old_path": "app.py"}],
        },
    )

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
    # 第二次触发调了 compare（拿改动文件集合）+ 又调了 MR changes（base..head 全量 diff）。
    gitlab.compare_refs.assert_awaited()
    # get_merge_request_changes 至少调用 2 次：首次全量 + 第二次增量仍走它。
    assert gitlab.get_merge_request_changes.await_count >= 2


@pytest.mark.asyncio
async def test_incremental_only_reviews_changed_files_diff_scope(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    """send to engine 的 diff_hunks 只包含本次 push 改动的文件（other.py 被过滤）。"""

    factory = session_factory_fixture
    await _seed_project(factory)

    # MR changes 有两个文件：app.py + other.py（都在 base..head 里），
    # compare.diffs 只包含 app.py —— orchestrator 应过滤掉 other.py。
    gitlab = _gitlab_mock(
        compare_response={
            "commits": [{"id": "c1"}],
            "diffs": [{"new_path": "app.py", "old_path": "app.py"}],
        },
        changes_files=["app.py", "other.py"],
    )
    engine = _StubEngine(
        [
            [],  # 第一次不管
            [],  # 第二次 engine 输出无所谓；断言点在收到的 context
        ]
    )
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    await orch.review_merge_request(_event(commit_sha="head-a"))
    await orch.review_merge_request(_event(commit_sha="head-b"))

    # 第一次：full，two files 都进 diff_hunks。
    assert set(engine.received_file_sets[0]) == {"app.py", "other.py"}
    # 第二次：incremental，只留 app.py。
    assert engine.received_file_sets[1] == ["app.py"]


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
async def test_incremental_rescans_changed_files_and_resolves_stale_discussions(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    """新 push 涉及 app.py：
      - app.py 的所有历史 open finding 全部标 resolved；
      - resolve_discussion 对每条历史 finding（有 gitlab_discussion_id 的）调用一次；
      - 本轮 app.py 的新 findings 全部走 create_merge_request_discussion 落新记录，
        并把返回的 discussion id 写回 finding 行；
      - other.py（不在改动集合）的老 finding 保持 open，不调 resolve_discussion。
    """

    factory = session_factory_fixture
    await _seed_project(factory)

    gitlab = _gitlab_mock(
        compare_response={
            "commits": [{"id": "c1"}],
            "diffs": [{"new_path": "app.py", "old_path": "app.py"}],
        },
        # 第二次审 base..head 也覆盖 app.py + other.py（other.py 会被过滤掉，
        # 但 MR changes 本身依旧带全 —— 更贴近真实 GitLab 语义）。
        changes_files=["app.py", "other.py"],
    )
    engine = _StubEngine(
        [
            # 第一次 push：app.py 与 other.py 各一条 finding。
            [
                _finding(file_path="app.py", line=2, rule_id="rule-a"),
                _finding(file_path="other.py", line=4, rule_id="rule-b"),
            ],
            # 第二次 push：只改 app.py；重审后 LLM 只报了新位置的问题。
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
    # 第一次 push 后应创建 2 条 discussion（disc-1 / disc-2），写回到 DB。
    first_findings = await _list_findings(factory)
    disc_ids_after_first = {
        (f.file_path, f.line_number): f.gitlab_discussion_id for f in first_findings
    }
    assert disc_ids_after_first[("app.py", 2)] == "disc-1"
    assert disc_ids_after_first[("other.py", 5)] == "disc-2"

    result = await orch.review_merge_request(_event(commit_sha="head-b"))

    # note 里"合并后总数" = 本次新增 1 + 未动文件历史 1 = 2。
    assert result.finding_count == 2

    findings = await _list_findings(factory)
    by_key = {(f.file_path, f.line_number, f.rule_id): f for f in findings}
    # app.py:2 老 finding 被 resolved（属于改动文件）。
    assert by_key[("app.py", 2, "rule-a")].status == "resolved"
    assert by_key[("app.py", 2, "rule-a")].resolved_in_review_id is not None
    # other.py:5 未动 → 保持 open。
    assert by_key[("other.py", 5, "rule-b")].status == "open"
    # app.py:20 本次新增，first_seen 指向第二次 review。
    reviews = await _list_reviews(factory)
    reviews_by_sha = {r.commit_sha: r for r in reviews}
    new_finding = by_key[("app.py", 20, "rule-c")]
    assert new_finding.first_seen_review_id == reviews_by_sha["head-b"].id
    assert new_finding.status == "open"
    # 新 finding 的 discussion id 也应被回写：本次 create 顺序上是第 3 条 → disc-3。
    assert new_finding.gitlab_discussion_id == "disc-3"

    # resolve_discussion：只对改动文件的历史 finding 调一次（app.py:2 → disc-1）。
    assert gitlab.resolve_discussion.await_count == 1
    resolved_call = gitlab.resolve_discussion.await_args_list[0].kwargs
    assert resolved_call["discussion_id"] == "disc-1"
    assert resolved_call["mr_iid"] == 7


@pytest.mark.asyncio
async def test_incremental_carries_over_untouched_file_findings_as_open(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    """A.java（本次没改）B.java（本次改了）都有老 finding；push 后 A.java 老
    finding 保持 open，B.java 老 finding 全 resolved，且 combined_findings 顺序
    与数量与 SummaryBuilder 期待对齐。
    """

    factory = session_factory_fixture
    await _seed_project(factory)

    gitlab = _gitlab_mock(
        compare_response={
            "commits": [{"id": "c1"}],
            "diffs": [{"new_path": "B.java", "old_path": "B.java"}],
        },
        # 第一次 push 覆盖两个文件；第二次 push 只改 B.java。
        changes_files=["A.java", "B.java"],
    )
    engine = _StubEngine(
        [
            [
                _finding(file_path="A.java", line=1, rule_id="ra1"),
                _finding(file_path="A.java", line=2, rule_id="ra2"),
                _finding(file_path="B.java", line=1, rule_id="rb1"),
                _finding(file_path="B.java", line=2, rule_id="rb2"),
            ],
            # 第二次 push：B.java 报一条新问题。
            [_finding(file_path="B.java", line=99, rule_id="rb-new")],
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

    # 合并总数 = 本次新增 1 (B.java:99) + 未动文件历史 2 (A.java x 2) = 3。
    assert result.finding_count == 3

    findings = await _list_findings(factory)
    by_key = {(f.file_path, f.line_number, f.rule_id): f for f in findings}
    # A.java 老 finding 保持 open。
    assert by_key[("A.java", 1, "ra1")].status == "open"
    assert by_key[("A.java", 2, "ra2")].status == "open"
    # B.java 老 finding 全 resolved。
    assert by_key[("B.java", 1, "rb1")].status == "resolved"
    assert by_key[("B.java", 2, "rb2")].status == "resolved"
    # 新的 B.java:99 落成 open。
    assert by_key[("B.java", 99, "rb-new")].status == "open"

    # resolve_discussion 只对 B.java 历史 finding（2 条）调用。
    assert gitlab.resolve_discussion.await_count == 2
    resolved_ids = sorted(
        c.kwargs["discussion_id"] for c in gitlab.resolve_discussion.await_args_list
    )
    # 第一次 push 顺序：A.java:1 → disc-1, A.java:2 → disc-2, B.java:1 → disc-3, B.java:2 → disc-4。
    assert resolved_ids == ["disc-3", "disc-4"]


@pytest.mark.asyncio
async def test_resolve_discussion_api_failure_does_not_block_flow(
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    """GitLab resolve API 抛异常 → warning，主流程继续，DB 侧 status 依然要标 resolved。"""

    factory = session_factory_fixture
    await _seed_project(factory)

    gitlab = _gitlab_mock(
        compare_response={
            "commits": [{"id": "c1"}],
            "diffs": [{"new_path": "app.py", "old_path": "app.py"}],
        },
    )
    # resolve_discussion 抛错。
    gitlab.resolve_discussion.side_effect = RuntimeError("network flaky")

    engine = _StubEngine(
        [
            [_finding(file_path="app.py", line=2, rule_id="rule-a")],
            [_finding(file_path="app.py", line=20, rule_id="rule-c")],
        ]
    )
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    await orch.review_merge_request(_event(commit_sha="head-a"))
    # 关键：即使 resolve 抛错，主流程也要完成，DB 也要 UPDATE resolved。
    result = await orch.review_merge_request(_event(commit_sha="head-b"))
    assert result.status == "done"

    findings = await _list_findings(factory)
    by_key = {(f.file_path, f.line_number, f.rule_id): f for f in findings}
    assert by_key[("app.py", 2, "rule-a")].status == "resolved"
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
