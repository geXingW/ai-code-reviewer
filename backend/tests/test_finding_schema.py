"""FindingRead 展示用冗余字段的 pydantic 层回归测试。

问题与误报页需要 project_name / project_id / mr_iid / mr_title /
review_created_at 五个冗余字段，用来在列表页快速定位到具体 MR。字段都是可选，
未 enrich 时应保持 None，保证老路径（不通过 admin _finding_to_read 的调用方）
仍能正确构造 FindingRead。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.finding import FindingRead

_BASE_PAYLOAD: dict[str, object] = {
    "id": "00000000-0000-0000-0000-000000000010",
    "review_id": "00000000-0000-0000-0000-000000000020",
    "file_path": "app/main.py",
    "line_number": 42,
    "rule_id": "rule-example",
    "severity": "WARNING",
    "title": "示例问题",
    "description": None,
    "suggestion": None,
    "existing_code": None,
    "confidence": 0.8,
    "gitlab_discussion_id": None,
    "fp_status": "NONE",
    "fp_marked_by": None,
    "fp_marked_at": None,
    "fp_marked_reason": None,
    "fp_reviewed_by": None,
    "fp_reviewed_at": None,
    "fp_review_note": None,
    "created_at": datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
    "updated_at": datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
}


def test_finding_read_accepts_mr_context_fields() -> None:
    """FindingRead 能承接项目 / MR 上下文冗余字段（enrich 后的正向路径）。"""

    payload = dict(_BASE_PAYLOAD)
    payload["project_name"] = "demo-project"
    payload["project_id"] = "00000000-0000-0000-0000-000000000030"
    payload["mr_iid"] = "42"
    payload["mr_title"] = "修复登录回调"
    payload["review_created_at"] = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

    read = FindingRead.model_validate(payload)
    assert read.project_name == "demo-project"
    assert str(read.project_id) == "00000000-0000-0000-0000-000000000030"
    assert read.mr_iid == "42"
    assert read.mr_title == "修复登录回调"
    assert read.review_created_at == datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def test_finding_read_defaults_context_fields_to_none() -> None:
    """未 enrich 时冗余字段默认 None，兼容不走 _finding_to_read 的老代码路径。"""

    read = FindingRead.model_validate(_BASE_PAYLOAD)
    assert read.project_name is None
    assert read.project_id is None
    assert read.mr_iid is None
    assert read.mr_title is None
    assert read.review_created_at is None


def test_finding_read_accepts_category_field() -> None:
    """PR-B: FindingRead 承接 LLM 输出的 category；缺失时保持 None。"""

    with_category = FindingRead.model_validate({**_BASE_PAYLOAD, "category": "security"})
    assert with_category.category == "security"

    without_category = FindingRead.model_validate(_BASE_PAYLOAD)
    assert without_category.category is None


def test_finding_create_accepts_category_field() -> None:
    """PR-B: FindingCreate 承接 category，且缺省为 None。"""

    from app.schemas.finding import FindingCreate

    base = {
        "review_id": _BASE_PAYLOAD["review_id"],
        "file_path": "app/main.py",
        "rule_id": "rule-example",
        "severity": "WARNING",
        "title": "示例问题",
    }
    with_category = FindingCreate.model_validate({**base, "category": "performance"})
    assert with_category.category == "performance"

    without_category = FindingCreate.model_validate(base)
    assert without_category.category is None
