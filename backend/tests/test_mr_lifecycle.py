"""MR 生命周期事件（close / merge / reopen）与 finding 状态联动的集成测试。

设计要点：
- 复用 :mod:`tests.test_incremental_review` 的 DB fixture 与工具函数，避免重复搭建
  一次真数据库；跨 MR 隔离测试也就顺便验证了 (project, mr_iid) 边界。
- Fake GitLab 客户端用 AsyncMock 造，方便断言 lifecycle 分支**没有**调用
  changes / note / status 等只跟审查流程相关的接口。
- 每个测试都跑真实 orchestrator.review_merge_request，不 stub 分流逻辑，保证
  "action='close' 走 lifecycle 分支"这条链路真实覆盖。
"""

from __future__ import annotations

from collections.abc import Sequence
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engines import Finding as EngineFinding
from app.engines import ReviewContext
from app.engines.registry import EngineRegistry
from app.models.finding import Finding as FindingRow
from app.models.review import Review as ReviewRow
from app.services.review_orchestrator import (
    GitLabMergeRequestEvent,
    ReviewOrchestrator,
)

# 复用 test_incremental_review 里的 fixture + 常量。session_factory_fixture 会
# drop→create→交回 factory，天然 per-test 隔离。
# noqa: F401 —— fixture 必须以模块级名称暴露给 pytest；ruff 看到"未使用"是误报。
from tests.test_incremental_review import (  # noqa: F401
    PROJECT_GITLAB_ID,
    _finding,
    _seed_project,
    session_factory_fixture,
)


class _StubEngine:
    """按 review 顺序依次返回预设 findings 的 engine 替身；耗尽后返回空列表。"""

    _NAME = "stub-engine"

    def __init__(self, findings_sequence: list[list[EngineFinding]]) -> None:
        self._sequence = list(findings_sequence)
        self.call_count = 0

    def name(self) -> str:
        return self._NAME

    async def review(self, context: ReviewContext) -> list[EngineFinding]:  # noqa: ARG002
        self.call_count += 1
        if not self._sequence:
            return []
        return list(self._sequence.pop(0))


def _registry(engine: _StubEngine) -> EngineRegistry:
    reg = EngineRegistry()
    reg.register(engine)  # type: ignore[arg-type]
    return reg


def _make_gitlab_mock() -> AsyncMock:
    """一个可满足 open/update 审查全流程的 GitLab 客户端 mock。

    - ``get_merge_request_changes`` 返回覆盖 app.py 一个文件的最小 diff；
    - ``create_merge_request_discussion`` 每次分配递增 discussion id，方便断言
      是否被调用了预期次数；
    - lifecycle 分支下的断言重点是 ``compare_refs`` / ``changes`` / ``note`` /
      ``set_commit_status`` 均**未被调用**。
    """

    counter = {"n": 0}

    async def _make_discussion(**_kwargs: object) -> dict[str, object]:
        counter["n"] += 1
        return {"id": f"disc-{counter['n']}"}

    client = AsyncMock()
    client.get_merge_request_changes.return_value = {
        "changes": [
            {
                "diff": "@@ -1,3 +1,4 @@\n line-a\n+new-app\n line-b\n",
                "new_path": "app.py",
                "old_path": "app.py",
                "new_file": False,
                "deleted_file": False,
            },
        ],
        "diff_refs": {"base_sha": "b", "start_sha": "s", "head_sha": "h"},
    }
    client.create_merge_request_note.return_value = {"id": 100}
    client.set_commit_status.return_value = {"status": "success"}
    client.create_merge_request_discussion.side_effect = _make_discussion
    client.resolve_discussion.return_value = {"resolved": True}
    # compare_refs 默认返回空 dict → is_ancestor False → orchestrator 若真进入
    # incremental 判定会保守降级为 full，不影响本套测试。
    client.compare_refs.return_value = {}
    return client


def _event(
    *,
    action: str,
    commit_sha: str = "head-a",
    mr_iid: int = 7,
) -> GitLabMergeRequestEvent:
    return GitLabMergeRequestEvent(
        project_id=PROJECT_GITLAB_ID,
        project_path="group/inc",
        mr_iid=mr_iid,
        source_branch="feature/x",
        target_branch="master",
        source_commit_sha=commit_sha,
        target_commit_sha="base-master",
        action=action,
        title="lifecycle MR",
        web_url="http://gitlab.example.com/mr/7",
    )


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
async def test_mr_closed_marks_open_findings_and_records_review(
    session_factory_fixture: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    """MR closed → 所有 open finding 变 mr_closed，插入一条 lifecycle 记账 review，
    且未调用 changes / note / commit_status。"""

    factory = session_factory_fixture
    await _seed_project(factory)

    # 第一次 open 审查产出 2 条 open finding。
    engine = _StubEngine(
        [
            [
                _finding(file_path="app.py", line=1, rule_id="rule-a"),
                _finding(file_path="app.py", line=2, rule_id="rule-b"),
            ]
        ]
    )
    gitlab = _make_gitlab_mock()
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    await orch.review_merge_request(_event(action="open"))

    # open 审查会调 changes / note / status —— 只关心 close 分支不再调。
    changes_calls_before = gitlab.get_merge_request_changes.await_count
    note_calls_before = gitlab.create_merge_request_note.await_count
    status_calls_before = gitlab.set_commit_status.await_count

    result = await orch.review_merge_request(_event(action="close"))

    assert result.status == "done"
    assert result.has_blocker is False
    # 受影响 finding 数应上抛到 result.finding_count 方便 webhook 层观测。
    assert result.finding_count == 2

    findings = await _list_findings(factory)
    # 两条 open finding 都翻成 mr_closed，并挂到 lifecycle review 上。
    assert all(f.status == "mr_closed" for f in findings)
    assert all(f.resolved_in_review_id is not None for f in findings)
    lifecycle_ids = {f.resolved_in_review_id for f in findings}
    assert len(lifecycle_ids) == 1

    # DB 里多了一条 lifecycle 记账 review：done / full / 0 finding / no blocker。
    reviews = await _list_reviews(factory)
    assert len(reviews) == 2
    lifecycle_review = reviews[-1]
    assert lifecycle_review.status == "done"
    assert lifecycle_review.review_mode == "full"
    assert lifecycle_review.finding_count == 0
    assert lifecycle_review.has_blocker is False
    assert lifecycle_review.parent_review_id is None
    assert lifecycle_review.id == lifecycle_ids.pop()

    # 关键：lifecycle 分支下这些 GitLab API 都**不再**被调用。
    assert gitlab.get_merge_request_changes.await_count == changes_calls_before
    assert gitlab.create_merge_request_note.await_count == note_calls_before
    assert gitlab.set_commit_status.await_count == status_calls_before
    gitlab.compare_refs.assert_not_awaited()


@pytest.mark.asyncio
async def test_mr_merged_resolves_open_findings(
    session_factory_fixture: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    """MR merged → 所有 open finding 变 resolved；resolved_in_review_id 指向 lifecycle
    记账 review。"""

    factory = session_factory_fixture
    await _seed_project(factory)

    engine = _StubEngine(
        [
            [
                _finding(file_path="app.py", line=1, rule_id="rule-a"),
                _finding(file_path="app.py", line=2, rule_id="rule-b"),
            ]
        ]
    )
    gitlab = _make_gitlab_mock()
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    await orch.review_merge_request(_event(action="open"))

    changes_calls_before = gitlab.get_merge_request_changes.await_count
    note_calls_before = gitlab.create_merge_request_note.await_count

    result = await orch.review_merge_request(_event(action="merge"))

    assert result.status == "done"
    assert result.finding_count == 2

    findings = await _list_findings(factory)
    assert all(f.status == "resolved" for f in findings)
    assert all(f.resolved_in_review_id is not None for f in findings)

    reviews = await _list_reviews(factory)
    lifecycle_review = reviews[-1]
    assert lifecycle_review.status == "done"
    # resolved_in_review_id 指向 lifecycle 记账 review。
    assert {f.resolved_in_review_id for f in findings} == {lifecycle_review.id}

    # merge 分支同样不调 changes / note / status。
    assert gitlab.get_merge_request_changes.await_count == changes_calls_before
    assert gitlab.create_merge_request_note.await_count == note_calls_before


@pytest.mark.asyncio
async def test_mr_reopened_flips_mr_closed_findings_back_to_open(
    session_factory_fixture: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    """open → close → reopen（携带新 head）：老 finding 翻回 open，reopen 会继续跑常规审查。

    reopen 用不同 head 更贴近真实场景（用户推了修复后 reopen），也让 ``_plan_review``
    发现"head 变了"从而走后续审查流程，能可靠断言 ``get_merge_request_changes`` 被调用。
    """

    factory = session_factory_fixture
    await _seed_project(factory)

    engine = _StubEngine(
        [
            [_finding(file_path="app.py", line=1, rule_id="rule-a")],
            # reopen 后的常规审查：stub 返回空 findings，保证不会因为新 finding
            # 干扰"老 finding 翻回 open"这条断言。
            [],
        ]
    )
    gitlab = _make_gitlab_mock()
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    await orch.review_merge_request(_event(action="open", commit_sha="head-a"))
    await orch.review_merge_request(_event(action="close", commit_sha="head-a"))
    # 确认 close 生效。
    findings_before = await _list_findings(factory)
    assert all(f.status == "mr_closed" for f in findings_before)

    # reopen 携带新 head → plan_review 走 compare_refs（mock 返回空 → 保守降级 full），
    # 常规审查流程真的会跑起来，get_merge_request_changes 会被再次调用。
    changes_before = gitlab.get_merge_request_changes.await_count
    await orch.review_merge_request(_event(action="reopen", commit_sha="head-b"))

    findings = await _list_findings(factory)
    # 老 finding 全翻回 open，resolved_in_review_id 被清空。
    old = [f for f in findings if f.rule_id == "rule-a"]
    assert len(old) == 1
    assert old[0].status == "open"
    assert old[0].resolved_in_review_id is None
    # reopen 走完常规流程 → changes 端点被再次调用（说明真的进入了正常审查路径）。
    assert gitlab.get_merge_request_changes.await_count == changes_before + 1


@pytest.mark.asyncio
async def test_new_mr_with_different_iid_does_not_affect_old_mr(
    session_factory_fixture: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    """MR#7 触发 open + close 后，MR#8 走独立会话；两组 finding 互不干扰。"""

    factory = session_factory_fixture
    await _seed_project(factory)

    engine = _StubEngine(
        [
            [_finding(file_path="app.py", line=1, rule_id="mr7-rule")],
            [_finding(file_path="other.py", line=1, rule_id="mr8-rule")],
        ]
    )
    gitlab = _make_gitlab_mock()
    orch = ReviewOrchestrator(
        gitlab_client=gitlab,
        engine_registry=_registry(engine),
        default_engine="stub-engine",
        session_factory=factory,
    )
    await orch.review_merge_request(_event(action="open", mr_iid=7))
    await orch.review_merge_request(_event(action="close", mr_iid=7))
    await orch.review_merge_request(_event(action="open", mr_iid=8))

    findings = await _list_findings(factory)
    by_rule = {f.rule_id: f for f in findings}
    # MR#7 的 finding：mr_closed。
    assert by_rule["mr7-rule"].status == "mr_closed"
    # MR#8 的 finding：open，不受 MR#7 影响。
    assert by_rule["mr8-rule"].status == "open"
    assert by_rule["mr8-rule"].resolved_in_review_id is None
