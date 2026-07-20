"""Unit tests for :mod:`app.core.summary_builder` — v2 分组视图。"""

from __future__ import annotations

from uuid import uuid4

from app.core.summary_builder import (
    build_finding_discussion_body,
    build_review_summary_note,
)
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


# ---------------------------------------------------------------------------
# discussion body 模板（PR-A）
# ---------------------------------------------------------------------------


def _finding_full(
    *,
    severity: str,
    rule_id: str,
    title: str = "sample",
    description: str | None = "desc",
    suggestion: str | None = None,
    existing_code: str | None = None,
) -> Finding:
    """构造 build_finding_discussion_body 用例的 Finding。"""

    return Finding(
        file_path="app.py",
        line_number=1,
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        title=title,
        description=description,
        suggestion=suggestion,
        existing_code=existing_code,
    )


def test_discussion_body_blocker_security_full_shape() -> None:
    """BLOCKER + 安全类 + 有 existing_code + code-like suggestion：完整走折叠模板。"""

    finding = _finding_full(
        severity="BLOCKER",
        rule_id="general.hardcoded-secret",
        title="硬编码密钥",
        description="检测到该行硬编码了 API Key。",
        existing_code='api_key = "sk-xxx"',
        suggestion='api_key = os.environ["OPENAI_API_KEY"]',
    )
    body = build_finding_discussion_body(finding)

    # 首行 severity emoji + category emoji + 中文标签 + title 都在。
    assert "🔴" in body
    assert "[BLOCKER]" in body
    assert "🔒" in body
    assert "安全" in body
    assert "硬编码密钥" in body
    # rule_id 用行内代码块单独一行。
    assert "`general.hardcoded-secret`" in body
    # description 原样出现。
    assert "检测到该行硬编码了 API Key。" in body
    # <details> 折叠 + before/after 都有。
    assert "<details><summary>💡 建议修复</summary>" in body
    assert "**当前代码：**" in body
    assert "**建议改为：**" in body
    assert 'api_key = "sk-xxx"' in body
    assert 'api_key = os.environ["OPENAI_API_KEY"]' in body
    # AI 声明脚注永远在末尾。
    assert "由 AI 生成" in body
    assert body.endswith("</sub>")


def test_discussion_body_warning_bug_emoji() -> None:
    """WARNING + 缺陷类：黄圆点 + 🐛 缺陷 出现。"""

    finding = _finding_full(
        severity="WARNING",
        rule_id="general.swallowed-exception",
        title="吞掉异常",
    )
    body = build_finding_discussion_body(finding)
    assert "🟡" in body
    assert "[WARNING]" in body
    assert "🐛" in body
    assert "缺陷" in body


def test_discussion_body_info_style_emoji() -> None:
    """INFO + 风格类：蓝圆点 + 🎨 风格 出现。"""

    finding = _finding_full(
        severity="INFO",
        rule_id="js.debug-leftover",
        title="遗留 console.log",
    )
    body = build_finding_discussion_body(finding)
    assert "🔵" in body
    assert "[INFO]" in body
    assert "🎨" in body
    assert "风格" in body


def test_discussion_body_text_suggestion_no_details() -> None:
    """只有一行文字 suggestion（不像代码）：不出 <details>，直接 **建议**：xxx。"""

    finding = _finding_full(
        severity="WARNING",
        rule_id="general.hardcoded-secret",
        suggestion="从环境变量读取",
        existing_code=None,
    )
    body = build_finding_discussion_body(finding)
    assert "<details>" not in body
    assert "**建议**：从环境变量读取" in body


def test_discussion_body_code_like_suggestion_forces_details() -> None:
    """suggestion 含 \\n 时视为代码，仍进 <details> fenced block。"""

    finding = _finding_full(
        severity="WARNING",
        rule_id="general.hardcoded-secret",
        suggestion="line1\nline2",
        existing_code=None,
    )
    body = build_finding_discussion_body(finding)
    assert "<details><summary>💡 建议修复</summary>" in body
    assert "**建议改为：**" in body
    assert "line1\nline2" in body


def test_discussion_body_all_empty_still_renders_header_and_footer() -> None:
    """description=None + suggestion=None + existing_code=None：仍保留头 + rule_id + 脚注。"""

    finding = _finding_full(
        severity="INFO",
        rule_id="general.magic-number",
        description=None,
        suggestion=None,
        existing_code=None,
    )
    body = build_finding_discussion_body(finding)
    assert "🔵" in body
    assert "[INFO]" in body
    assert "`general.magic-number`" in body
    assert "<details>" not in body  # 没建议内容不出折叠
    assert "**建议**" not in body
    assert "由 AI 生成" in body


def test_discussion_body_unknown_severity_and_rule_id_fallback() -> None:
    """未知 severity/rule_id：兜底到 ⚪ + 📝 其他，不抛异常。"""

    finding = _finding_full(
        severity="INFO",  # Finding 的 Literal 只允许三档；用 INFO 但把 rule_id 未知
        rule_id="totally-unknown",
        description=None,
        suggestion=None,
    )
    body = build_finding_discussion_body(finding)
    # 未知 rule_id → OTHER
    assert "📝" in body
    assert "其他" in body


# ---------------------------------------------------------------------------
# summary_note 分布行
# ---------------------------------------------------------------------------


def test_summary_note_severity_distribution_all_three() -> None:
    """3 BLOCKER + 5 WARNING + 8 INFO：严重度分布行按固定顺序全部出现。"""

    findings: list[Finding] = []
    for i in range(3):
        findings.append(
            _finding(
                file_path="a.py", line=i, severity="BLOCKER",
                title=f"b{i}", rule_id="general.hardcoded-secret",
            )
        )
    for i in range(5):
        findings.append(
            _finding(
                file_path="a.py", line=100 + i, severity="WARNING",
                title=f"w{i}", rule_id="general.swallowed-exception",
            )
        )
    for i in range(8):
        findings.append(
            _finding(
                file_path="a.py", line=200 + i, severity="INFO",
                title=f"i{i}", rule_id="js.debug-leftover",
            )
        )
    body = _build(findings, new_findings=findings, carried_findings=[])
    assert "🔴 BLOCKER: 3" in body
    assert "🟡 WARNING: 5" in body
    assert "🔵 INFO: 8" in body
    # 严重度顺序：BLOCKER 在 WARNING 前，WARNING 在 INFO 前。
    idx_b = body.index("🔴 BLOCKER: 3")
    idx_w = body.index("🟡 WARNING: 5")
    idx_i = body.index("🔵 INFO: 8")
    assert idx_b < idx_w < idx_i


def test_summary_note_severity_distribution_skips_zero_counts() -> None:
    """全部 WARNING：只出现 🟡 WARNING，不出现 0 计数档次。"""

    findings = [
        _finding(file_path="a.py", line=i, severity="WARNING", title=f"w{i}")
        for i in range(4)
    ]
    body = _build(findings, new_findings=findings, carried_findings=[])
    assert "🟡 WARNING: 4" in body
    assert "BLOCKER: 0" not in body
    assert "INFO: 0" not in body
    assert "🔴" not in body.split("- Blocking findings")[0]
    assert "🔵" not in body.split("- Blocking findings")[0]


def test_summary_note_no_distribution_when_findings_empty() -> None:
    """空 findings：不出现分布行。"""

    body = _build([], new_findings=[], carried_findings=[])
    assert "BLOCKER:" not in body
    assert "WARNING:" not in body
    assert "INFO:" not in body


def test_summary_note_category_distribution_orders_by_count_then_stable() -> None:
    """构造 3 类 finding：验证 count 降序 + 类别稳定序。"""

    findings = [
        # security x 1
        _finding(
            file_path="a.py", line=1, severity="BLOCKER",
            title="s1", rule_id="general.hardcoded-secret",
        ),
        # bug x 3
        _finding(
            file_path="a.py", line=2, severity="WARNING",
            title="b1", rule_id="general.swallowed-exception",
        ),
        _finding(
            file_path="a.py", line=3, severity="WARNING",
            title="b2", rule_id="general.swallowed-exception",
        ),
        _finding(
            file_path="a.py", line=4, severity="WARNING",
            title="b3", rule_id="general.swallowed-exception",
        ),
        # performance x 2
        _finding(
            file_path="a.py", line=5, severity="INFO",
            title="p1", rule_id="backend.n-plus-one",
        ),
        _finding(
            file_path="a.py", line=6, severity="INFO",
            title="p2", rule_id="backend.n-plus-one",
        ),
    ]
    body = _build(findings, new_findings=findings, carried_findings=[])
    # count 降序：缺陷 3 > 性能 2 > 安全 1
    idx_bug = body.index("🐛 缺陷: 3")
    idx_perf = body.index("⚡ 性能: 2")
    idx_sec = body.index("🔒 安全: 1")
    assert idx_bug < idx_perf < idx_sec


def test_summary_note_category_distribution_stable_when_counts_tie() -> None:
    """两类 count 相同：按 SECURITY → BUG → ... 稳定顺序展示。"""

    findings = [
        _finding(
            file_path="a.py", line=1, severity="WARNING",
            title="b1", rule_id="general.swallowed-exception",  # BUG
        ),
        _finding(
            file_path="a.py", line=2, severity="WARNING",
            title="s1", rule_id="general.hardcoded-secret",  # SECURITY
        ),
    ]
    body = _build(findings, new_findings=findings, carried_findings=[])
    # 相同 count 1：SECURITY 在 BUG 前
    idx_sec = body.index("🔒 安全: 1")
    idx_bug = body.index("🐛 缺陷: 1")
    assert idx_sec < idx_bug


# ---------------------------------------------------------------------------
# _render_findings_section 前缀
# ---------------------------------------------------------------------------


def test_findings_section_prefixes_each_item_with_severity_and_category_emoji() -> None:
    """每条 finding 前带 severity emoji + category emoji。"""

    findings = [
        _finding(
            file_path="a.py", line=1, severity="BLOCKER",
            title="secret", rule_id="general.hardcoded-secret",
        ),
    ]
    body = _build(findings, new_findings=findings, carried_findings=[])
    assert "- 🔴 🔒 **[BLOCKER] secret**" in body
