"""ReviewRead / ReviewCreate schema-level 单元测试（PR #89 增量审查字段）。

只覆盖 pydantic 层：不引入 SQLAlchemy 会话，也不启动 FastAPI TestClient；
纯 model_validate 校验新字段 ``base_sha`` / ``parent_review_id`` / ``review_mode``
能被正确接收并回显。

背景：PR #89 在 Review model 加了这三个字段，本 PR 把 schema 层暴露给前端。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.review import ReviewCreate, ReviewRead


def _make_review_orm_like(
    *,
    base_sha: str | None,
    parent_review_id: UUID | None,
    review_mode: str,
) -> SimpleNamespace:
    """构造一个 ORM-like 对象（走 ReviewRead 的 from_attributes 路径）。

    只填 ReviewRead 必需的字段；不模拟 SQLAlchemy relationship（project_name /
    rules_used 由 admin API 层填充，schema 默认值即可）。
    """

    now = datetime.now(tz=UTC)
    return SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        mr_iid="42",
        source_branch="feature/x",
        target_branch="master",
        commit_sha="abcdef0123456789",
        status="done",
        engine_used="llm-direct",
        provider_used="openai",
        policy_applied=None,
        has_blocker=False,
        finding_count=0,
        duration_ms=1234,
        raw_llm_output=None,
        base_sha=base_sha,
        parent_review_id=parent_review_id,
        review_mode=review_mode,
        created_at=now,
        updated_at=now,
    )


def test_review_read_accepts_incremental_fields() -> None:
    """ReviewRead 能承接 PR #89 新字段（增量场景：三字段全填）。"""

    parent_id = uuid4()
    orm_like = _make_review_orm_like(
        base_sha="deadbeef000000000000000000000000",
        parent_review_id=parent_id,
        review_mode="incremental",
    )

    read = ReviewRead.model_validate(orm_like)

    assert read.review_mode == "incremental"
    assert read.base_sha == "deadbeef000000000000000000000000"
    assert read.parent_review_id == parent_id


def test_review_read_full_mode_allows_null_link_fields() -> None:
    """全量审查：base_sha / parent_review_id 都是 None，review_mode='full'。"""

    orm_like = _make_review_orm_like(
        base_sha=None,
        parent_review_id=None,
        review_mode="full",
    )

    read = ReviewRead.model_validate(orm_like)

    assert read.review_mode == "full"
    assert read.base_sha is None
    assert read.parent_review_id is None


def test_review_mode_rejects_unknown_literal() -> None:
    """review_mode 非合法字面量（e.g. 'partial'）→ ValidationError。

    这条断言防止后端 orchestrator 意外写入非法值，也提醒后续新增 mode 时
    要一并更新 ReviewMode Literal。
    """

    orm_like = _make_review_orm_like(
        base_sha=None,
        parent_review_id=None,
        review_mode="partial",  # 非法
    )

    with pytest.raises(ValidationError):
        ReviewRead.model_validate(orm_like)


def test_review_create_defaults_to_full_mode() -> None:
    """ReviewCreate 未显式给 review_mode → 默认为 'full'。"""

    create = ReviewCreate(
        project_id=uuid4(),
        mr_iid="1",
        source_branch="feat/a",
        target_branch="master",
        commit_sha="1234567",
    )

    assert create.review_mode == "full"
    assert create.base_sha is None
    assert create.parent_review_id is None
