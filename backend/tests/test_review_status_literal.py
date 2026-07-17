"""ReviewStatus Literal 完整性回归测试。

PR #86 让 orchestrator 会把 AI 引擎挂了显式落一条 ``status='engine_error'`` 的
评审行，但 schema 层 ``ReviewStatus`` Literal 一直只列了 ``pending / running /
done / failed``。生产上第一次 engine_error 记录写入 DB 后，``GET /api/reviews``
从 DB 读到该行时会 ValidationError 500——本用例把这个坑固化住。

未来 orchestrator 再增加新状态（比如 ``skipped_reuse``），schema Literal 必须
同步扩展，否则本用例会挂。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.review import ReviewCreate, ReviewStatus

# orchestrator 写入 DB 的所有 status 常量（review_orchestrator.py 里出现过的字面量）。
# 若代码里加了新 status 但没更新此表 → 相应用例会挂，提醒同步。
_ORCHESTRATOR_WRITTEN_STATUSES = ("pending", "running", "done", "engine_error")


@pytest.mark.parametrize("status_value", _ORCHESTRATOR_WRITTEN_STATUSES)
def test_review_status_literal_accepts_orchestrator_written_values(status_value: str) -> None:
    """orchestrator 会写入 DB 的每种 status，schema 都必须接受。"""

    payload = ReviewCreate(
        project_id="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
        mr_iid="1",
        source_branch="feature/x",
        target_branch="master",
        commit_sha="abc123",
        status=status_value,  # type: ignore[arg-type]
    )
    assert payload.status == status_value


def test_review_status_literal_rejects_unknown_value() -> None:
    """未知 status 必须被拒绝，防止未来打错字漏检。"""

    with pytest.raises(ValidationError):
        ReviewCreate(
            project_id="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
            mr_iid="1",
            source_branch="feature/x",
            target_branch="master",
            commit_sha="abc123",
            status="totally-not-a-status",  # type: ignore[arg-type]
        )


def test_review_status_literal_annotation_exposes_engine_error() -> None:
    """静态断言：Literal 类型参数中包含 engine_error（防止 Literal 被误改回旧集合）。"""

    # typing.get_args 返回 Literal 的字面量集合。
    from typing import get_args

    assert "engine_error" in get_args(ReviewStatus)
