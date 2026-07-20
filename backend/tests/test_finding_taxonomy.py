"""Unit tests for :mod:`app.core.finding_taxonomy`.

覆盖 42 条基础规则的显式映射、前缀兜底、以及 severity/category display
的展示元数据。所有测试都是纯函数级别，无依赖。
"""

from __future__ import annotations

import pytest

from app.core.finding_taxonomy import (
    FindingCategory,
    category_display,
    infer_category,
    severity_display,
)

# 42 条基础规则的显式映射，每条至少一个 sample。任何一条改分类都会命中失败。
_EXPLICIT_RULES: list[tuple[str, FindingCategory]] = [
    # 安全类
    ("general.hardcoded-secret", FindingCategory.SECURITY),
    ("general.sql-injection", FindingCategory.SECURITY),
    ("general.log-sensitive-info", FindingCategory.SECURITY),
    ("python.fstring-sql", FindingCategory.SECURITY),
    ("frontend.xss-innerHTML", FindingCategory.SECURITY),
    # 缺陷类
    ("general.swallowed-exception", FindingCategory.BUG),
    ("python.exception-handling", FindingCategory.BUG),
    ("python.mutable-default-arg", FindingCategory.BUG),
    ("java.null-safety", FindingCategory.BUG),
    ("java.equals-hashcode", FindingCategory.BUG),
    ("react.state-mutation", FindingCategory.BUG),
    ("react.effect-deps", FindingCategory.BUG),
    ("react.list-key", FindingCategory.BUG),
    ("backend.thread-safety", FindingCategory.BUG),
    # 性能类
    ("backend.n-plus-one", FindingCategory.PERFORMANCE),
    ("backend.unbounded-query", FindingCategory.PERFORMANCE),
    ("backend.resource-leak", FindingCategory.PERFORMANCE),
    ("backend.cache-invalidation", FindingCategory.PERFORMANCE),
    ("backend.retry-without-backoff", FindingCategory.PERFORMANCE),
    ("java.stream-close", FindingCategory.PERFORMANCE),
    ("java.string-concat-loop", FindingCategory.PERFORMANCE),
    ("python.async-blocking", FindingCategory.PERFORMANCE),
    ("react.effect-cleanup", FindingCategory.PERFORMANCE),
    ("frontend.large-bundle-import", FindingCategory.PERFORMANCE),
    # 可维护性
    ("backend.transaction-boundary", FindingCategory.MAINTAINABILITY),
    ("backend.breaking-api-change", FindingCategory.MAINTAINABILITY),
    ("backend.missing-input-validation", FindingCategory.MAINTAINABILITY),
    ("backend.timezone-naive", FindingCategory.MAINTAINABILITY),
    ("backend.log-level-abuse", FindingCategory.MAINTAINABILITY),
    ("general.long-method", FindingCategory.MAINTAINABILITY),
    ("general.magic-number", FindingCategory.MAINTAINABILITY),
    ("general.todo-in-critical-path", FindingCategory.MAINTAINABILITY),
    ("java.lombok-data-jpa", FindingCategory.MAINTAINABILITY),
    ("python.type-hint-missing", FindingCategory.MAINTAINABILITY),
    ("react.large-inline-render", FindingCategory.MAINTAINABILITY),
    ("frontend.hardcoded-api-url", FindingCategory.MAINTAINABILITY),
    ("frontend.missing-loading-error-state", FindingCategory.MAINTAINABILITY),
    # 风格
    ("general.commented-code", FindingCategory.STYLE),
    ("js.debug-leftover", FindingCategory.STYLE),
    ("frontend.i18n-hardcoded-copy", FindingCategory.STYLE),
    ("ts.any-abuse", FindingCategory.STYLE),
    ("ts.non-null-assertion", FindingCategory.STYLE),
]


@pytest.mark.parametrize("rule_id,expected", _EXPLICIT_RULES)
def test_infer_category_explicit_mapping(
    rule_id: str, expected: FindingCategory
) -> None:
    """每条 rule_id 都精确映射到 spec 里写死的类别。"""

    assert infer_category(rule_id) == expected


def test_infer_category_explicit_map_count() -> None:
    """确保 spec 承诺的 42 条基础规则数量与本测试一致。"""

    assert len(_EXPLICIT_RULES) == 42


def test_infer_category_prefix_fallback_react() -> None:
    """未显式映射的 react.* 走前缀兜底到 MAINTAINABILITY。"""

    assert infer_category("react.foo-not-mapped") == FindingCategory.MAINTAINABILITY


def test_infer_category_prefix_fallback_general_other() -> None:
    """general 前缀兜底到 OTHER（不敢默认成任何具体类别）。"""

    assert infer_category("general.xxx-not-mapped") == FindingCategory.OTHER


def test_infer_category_prefix_fallback_js_style() -> None:
    """js.* 前缀兜底到 STYLE。"""

    assert infer_category("js.some-new-check") == FindingCategory.STYLE


def test_infer_category_empty_string_returns_other() -> None:
    """空 rule_id 一律 OTHER，避免异常传播。"""

    assert infer_category("") == FindingCategory.OTHER


def test_infer_category_no_prefix_returns_other() -> None:
    """没有 `.` 分隔符也就没有前缀，走兜底 OTHER。"""

    assert infer_category("weirdid") == FindingCategory.OTHER


def test_infer_category_unknown_prefix_returns_other() -> None:
    """未知前缀也走 OTHER。"""

    assert infer_category("cobol.some-rule") == FindingCategory.OTHER


def test_severity_display_all_three_known_levels() -> None:
    """三档标准 severity 的 emoji + label 组合固定。"""

    assert severity_display("BLOCKER") == ("🔴", "BLOCKER")
    assert severity_display("WARNING") == ("🟡", "WARNING")
    assert severity_display("INFO") == ("🔵", "INFO")


def test_severity_display_case_insensitive() -> None:
    """小写传入也能识别（内部会 upper）。"""

    assert severity_display("blocker") == ("🔴", "BLOCKER")


def test_severity_display_unknown_returns_neutral_dot() -> None:
    """未知 severity 用中性圆点占位，不抛。"""

    emoji, label = severity_display("HYPE")
    assert emoji == "⚪"
    assert label == "HYPE"


@pytest.mark.parametrize(
    "category,expected_emoji,expected_label",
    [
        (FindingCategory.SECURITY, "🔒", "安全"),
        (FindingCategory.BUG, "🐛", "缺陷"),
        (FindingCategory.PERFORMANCE, "⚡", "性能"),
        (FindingCategory.MAINTAINABILITY, "🔧", "可维护性"),
        (FindingCategory.STYLE, "🎨", "风格"),
        (FindingCategory.OTHER, "📝", "其他"),
    ],
)
def test_category_display_all_six_categories(
    category: FindingCategory, expected_emoji: str, expected_label: str
) -> None:
    """6 类枚举全覆盖，emoji + 中文标签都对得上 spec。"""

    emoji, label = category_display(category)
    assert emoji == expected_emoji
    assert label == expected_label
