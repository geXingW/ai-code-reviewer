"""Unit tests for :mod:`app.core.summary_builder` — v2 分组视图。"""

from __future__ import annotations

from uuid import uuid4

from app.core.summary_builder import build_review_summary_note
from app.engines import Finding


def _finding(
    *,
    file_path: str = "app/foo.py",
    line: int | None = 10,
    severity: str = "WARNING",
    title: str = "sample title",
    rule_id: str = "rule-x",
    description: str | None = "desc",
    suggestion: str | None = "fix it",
) -> Finding:
    """构造用例专用的最小 Finding，只填测试关心的字段。"""

    return Finding(
        file_path=file_path,
        line_number=line,
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        title=title,
        description=description,
        suggestion=suggestion,
    )


def _build(
    findings: list[Finding],
    *,
    new_findings: list[Finding] | None = None,
    carried_findings: list[Finding] | None = None,
    engine_error: str | None = None,
) -> str:
    """所有测试统一走这个入口，减少样板。"""

    return build_review_summary_note(
        review_id=uuid4(),
        findings=findings,
        has_blocker=False,
        blocker_count=0,
        policy_applied=None,
        detail_url=None,
        engine_error=engine_error,
        new_findings=new_findings,
        carried_findings=carried_findings,
    )


def test_summary_v2_full_mode_shows_only_new_section() -> None:
    """full 模式：new 传入 5 条、carried 传空，只出新增段，不出历史段。"""

    new = [
        _finding(file_path="a.py", line=30, severity="WARNING", title="a1"),
        _finding(file_path="a.py", line=10, severity="BLOCKER", title="a2"),
        _finding(file_path="a.py", line=20, severity="INFO", title="a3"),
        _finding(file_path="b.py", line=5, severity="WARNING", title="b1"),
        _finding(file_path="b.py", line=15, severity="INFO", title="b2"),
    ]
    body = _build([*new], new_findings=new, carried_findings=[])

    assert "🆕 本次新增（5 条）" in body
    assert "📌 历史遗留" not in body
    # 每个文件里的顺序按 line 升序，取 a.py 段验证。
    a_section = body.split("#### `a.py`", 1)[1].split("####", 1)[0]
    idx_10 = a_section.index("**[BLOCKER] a2**")
    idx_20 = a_section.index("**[INFO] a3**")
    idx_30 = a_section.index("**[WARNING] a1**")
    assert idx_10 < idx_20 < idx_30


def test_summary_v2_incremental_shows_both_sections() -> None:
    """incremental：new 3 条（跨 2 文件），carried 2 条（1 文件）。"""

    new = [
        _finding(file_path="new1.py", line=1, title="n1"),
        _finding(file_path="new1.py", line=2, title="n2"),
        _finding(file_path="new2.py", line=1, title="n3"),
    ]
    carried = [
        _finding(file_path="old.py", line=1, title="c1"),
        _finding(file_path="old.py", line=2, title="c2"),
    ]
    body = _build([*new, *carried], new_findings=new, carried_findings=carried)

    assert "🆕 本次新增（3 条）" in body
    assert "📌 历史遗留（未改动文件，2 条）" in body
    # 历史段前必须带 blockquote 说明。
    assert "> 以下问题所在文件本次 push 未改动，保留自上一次审查。" in body
    # blockquote 出现在历史段标题之后，用位置断言。
    idx_carried_header = body.index("📌 历史遗留")
    idx_blockquote = body.index("> 以下问题所在文件")
    assert idx_carried_header < idx_blockquote


def test_summary_v2_no_findings_returns_no_findings_message() -> None:
    """两段都空：兼容旧版 "No findings" 文案。"""

    body = _build([], new_findings=[], carried_findings=[])
    assert "No findings were reported by the configured engine." in body
    assert "🆕 本次新增" not in body
    assert "📌 历史遗留" not in body


def test_summary_v2_file_grouping_orders_by_severity() -> None:
    """一个文件有 blocker，另一个全 warning：blocker 文件靠前。"""

    findings = [
        _finding(file_path="warn.py", line=1, severity="WARNING", title="w1"),
        _finding(file_path="warn.py", line=2, severity="WARNING", title="w2"),
        _finding(file_path="hot.py", line=1, severity="BLOCKER", title="h1"),
        _finding(file_path="hot.py", line=2, severity="INFO", title="h2"),
    ]
    body = _build(findings, new_findings=findings, carried_findings=[])

    idx_hot = body.index("#### `hot.py`")
    idx_warn = body.index("#### `warn.py`")
    assert idx_hot < idx_warn


def test_summary_v2_finding_without_line_number_omits_line_field() -> None:
    """line_number=None 时不能出现 Location: None / 空 Location 行。"""

    findings = [_finding(file_path="x.py", line=None, title="no-line")]
    body = _build(findings, new_findings=findings, carried_findings=[])

    assert "no-line" in body
    assert "Location: None" not in body
    # 该 finding 唯一一条，且无 line ⇒ 不应出现任何 Location 字段。
    assert "Location: `" not in body


def test_summary_v2_backward_compatible_when_no_new_carried_args() -> None:
    """调用方不传 new/carried：把 findings 当作新增分区展示，条数一致。"""

    findings = [
        _finding(file_path="only.py", line=1, title="t1"),
        _finding(file_path="only.py", line=2, title="t2"),
    ]
    body = _build(findings)

    assert "🆕 本次新增（2 条）" in body
    assert "📌 历史遗留" not in body
    assert "**[WARNING] t1**" in body
    assert "**[WARNING] t2**" in body


def test_summary_v2_engine_error_branch_unchanged() -> None:
    """engine_error 分支保留 FAILED 标题，不受本 PR 分区改动影响。"""

    body = build_review_summary_note(
        review_id=uuid4(),
        findings=[],
        has_blocker=False,
        blocker_count=0,
        policy_applied=None,
        detail_url=None,
        engine_error="engine died",
    )
    assert "# ⚠️ AI Review FAILED" in body
    assert "engine died" in body
    assert "🆕 本次新增" not in body
    assert "📌 历史遗留" not in body
