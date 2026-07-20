"""Finding 展示元数据：severity emoji + rule_id → category 分类。

不依赖 DB，纯 Python 映射。用于渲染 GitLab 评论时的视觉标签。
后续 PR-B 在 Finding schema 加 category 字段后，这个推断函数会退化为
「LLM 未输出时的 fallback」。
"""

from __future__ import annotations

from enum import StrEnum


class FindingCategory(StrEnum):
    """Finding 大类。参考 alibaba/open-code-review 的 8 分类简化到 6 类。"""

    SECURITY = "security"          # 🔒 安全
    BUG = "bug"                    # 🐛 缺陷
    PERFORMANCE = "performance"    # ⚡ 性能
    MAINTAINABILITY = "maintainability"  # 🔧 可维护性
    STYLE = "style"                # 🎨 风格
    OTHER = "other"                # 📝 其他


# severity → (emoji, 中文标签)
_SEVERITY_DISPLAY: dict[str, tuple[str, str]] = {
    "BLOCKER": ("🔴", "BLOCKER"),
    "WARNING": ("🟡", "WARNING"),
    "INFO": ("🔵", "INFO"),
}

# category → (emoji, 中文标签)
_CATEGORY_DISPLAY: dict[FindingCategory, tuple[str, str]] = {
    FindingCategory.SECURITY: ("🔒", "安全"),
    FindingCategory.BUG: ("🐛", "缺陷"),
    FindingCategory.PERFORMANCE: ("⚡", "性能"),
    FindingCategory.MAINTAINABILITY: ("🔧", "可维护性"),
    FindingCategory.STYLE: ("🎨", "风格"),
    FindingCategory.OTHER: ("📝", "其他"),
}

# rule_id 明确映射到具体类别（覆盖 42 条基础规则）。
# 命中优先于前缀推断；未命中的走 _PREFIX_CATEGORY 兜底。
_RULE_ID_CATEGORY: dict[str, FindingCategory] = {
    # 安全类
    "general.hardcoded-secret": FindingCategory.SECURITY,
    "general.sql-injection": FindingCategory.SECURITY,
    "general.log-sensitive-info": FindingCategory.SECURITY,
    "python.fstring-sql": FindingCategory.SECURITY,
    "frontend.xss-innerHTML": FindingCategory.SECURITY,
    # 缺陷类（会导致运行时错误的）
    "general.swallowed-exception": FindingCategory.BUG,
    "python.exception-handling": FindingCategory.BUG,
    "python.mutable-default-arg": FindingCategory.BUG,
    "java.null-safety": FindingCategory.BUG,
    "java.equals-hashcode": FindingCategory.BUG,
    "react.state-mutation": FindingCategory.BUG,
    "react.effect-deps": FindingCategory.BUG,
    "react.list-key": FindingCategory.BUG,
    "backend.thread-safety": FindingCategory.BUG,
    # 性能类
    "backend.n-plus-one": FindingCategory.PERFORMANCE,
    "backend.unbounded-query": FindingCategory.PERFORMANCE,
    "backend.resource-leak": FindingCategory.PERFORMANCE,
    "backend.cache-invalidation": FindingCategory.PERFORMANCE,
    "backend.retry-without-backoff": FindingCategory.PERFORMANCE,
    "java.stream-close": FindingCategory.PERFORMANCE,
    "java.string-concat-loop": FindingCategory.PERFORMANCE,
    "python.async-blocking": FindingCategory.PERFORMANCE,
    "react.effect-cleanup": FindingCategory.PERFORMANCE,
    "frontend.large-bundle-import": FindingCategory.PERFORMANCE,
    # 可维护性
    "backend.transaction-boundary": FindingCategory.MAINTAINABILITY,
    "backend.breaking-api-change": FindingCategory.MAINTAINABILITY,
    "backend.missing-input-validation": FindingCategory.MAINTAINABILITY,
    "backend.timezone-naive": FindingCategory.MAINTAINABILITY,
    "backend.log-level-abuse": FindingCategory.MAINTAINABILITY,
    "general.long-method": FindingCategory.MAINTAINABILITY,
    "general.magic-number": FindingCategory.MAINTAINABILITY,
    "general.todo-in-critical-path": FindingCategory.MAINTAINABILITY,
    "java.lombok-data-jpa": FindingCategory.MAINTAINABILITY,
    "python.type-hint-missing": FindingCategory.MAINTAINABILITY,
    "react.large-inline-render": FindingCategory.MAINTAINABILITY,
    "frontend.hardcoded-api-url": FindingCategory.MAINTAINABILITY,
    "frontend.missing-loading-error-state": FindingCategory.MAINTAINABILITY,
    # 风格
    "general.commented-code": FindingCategory.STYLE,
    "js.debug-leftover": FindingCategory.STYLE,
    "frontend.i18n-hardcoded-copy": FindingCategory.STYLE,
    "ts.any-abuse": FindingCategory.STYLE,
    "ts.non-null-assertion": FindingCategory.STYLE,
}

# rule_id 前缀 → 兜底类别（未在 _RULE_ID_CATEGORY 明确映射时使用）
_PREFIX_CATEGORY: dict[str, FindingCategory] = {
    "general": FindingCategory.OTHER,
    "backend": FindingCategory.MAINTAINABILITY,
    "python": FindingCategory.MAINTAINABILITY,
    "java": FindingCategory.MAINTAINABILITY,
    "js": FindingCategory.STYLE,
    "frontend": FindingCategory.MAINTAINABILITY,
    "react": FindingCategory.MAINTAINABILITY,
    "ts": FindingCategory.STYLE,
}


def infer_category(rule_id: str) -> FindingCategory:
    """从 rule_id 推断分类。

    优先精确匹配 _RULE_ID_CATEGORY，其次按前缀兜底 _PREFIX_CATEGORY，
    最后 OTHER。空字符串或 None 一律返回 OTHER。
    """

    if not rule_id:
        return FindingCategory.OTHER
    if rule_id in _RULE_ID_CATEGORY:
        return _RULE_ID_CATEGORY[rule_id]
    prefix = rule_id.split(".", 1)[0]
    return _PREFIX_CATEGORY.get(prefix, FindingCategory.OTHER)


def severity_display(severity: str) -> tuple[str, str]:
    """返回 (emoji, label)。未知 severity 用中性圆点。"""

    return _SEVERITY_DISPLAY.get(severity.upper(), ("⚪", severity.upper()))


def category_display(category: FindingCategory) -> tuple[str, str]:
    """返回 (emoji, 中文标签)。"""

    return _CATEGORY_DISPLAY[category]
