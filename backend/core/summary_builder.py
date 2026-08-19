"""Markdown builders for GitLab review feedback."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import TypedDict
from uuid import UUID

from core.finding_taxonomy import (
    FindingCategory,
    category_display,
    infer_category,
    severity_display,
)
from engines import Finding

# AI 声明脚注：在每条行级 discussion 尾部渲染成小字，提示这是 AI 输出。
_AI_FOOTER = (
    "<sub>🤖 由 AI 生成，可能存在误报。"
    "请通过管理后台反馈误报以帮助模型收敛。</sub>"
)

# 判断 suggestion 文本"看起来像代码"的启发式：包含换行，或者以典型的语言
# 关键词开头（考虑 py/js/ts/java 常见起手式），或包含赋值/成员访问/调用等
# 明显的代码结构。命中即用 fenced block 渲染，否则视为普通建议文字直接
# 一行输出，避免"从环境变量读取"这种一句话被折叠塞进代码块。
_CODE_LIKE_PATTERN = re.compile(
    r"^\s*(def |class |function |const |let |var |import |from |"
    r"if |for |while |return |# |// |@|<)"
)

# 结构性代码信号（对单行 suggestion 生效）：``x = y`` / ``obj.method(...)`` / 括号调用等。
_CODE_STRUCTURAL_PATTERN = re.compile(r"[=({\[;]|\w+\.\w+")


class SkippedFileEntry(TypedDict):
    """被跳过文件的摘要条目（对应 ``engines.types.SkippedFile.model_dump()``）。

    用 TypedDict 而不是直接 import ``SkippedFile``，避免 ``core`` -> ``engines``
    的循环依赖；调用方（orchestrator）传 ``[s.model_dump() for s in ...]`` 进来。
    """

    file_path: str
    reason: str
    detail: str | None


def _suggestion_looks_like_code(text: str) -> bool:
    """启发式判断 suggestion 是否是代码片段。"""

    if not text:
        return False
    if "\n" in text:
        return True
    if _CODE_LIKE_PATTERN.match(text):
        return True
    return bool(_CODE_STRUCTURAL_PATTERN.search(text))


def _resolve_finding_category(finding: Finding) -> FindingCategory:
    """优先用 ``finding.category``（LLM 输出）；无效或缺失时 fallback 到 rule_id 推断。

    PR-B 之后 finding.category 是一等公民字段，但 DB / API 边界为了兼容老数据
    与 LLM 偶发"发挥"，允许存储任意字符串或 None。这里把这三种情况都收敛成
    一个 :class:`FindingCategory`：

    - 有值且能解析成合法枚举 → 直接返回；
    - 有值但不在枚举内（或类型不对）→ 忽略，走 rule_id 推断；
    - 为 None / 空 → 走 rule_id 推断。
    """

    raw = getattr(finding, "category", None)
    if raw:
        try:
            return FindingCategory(raw)
        except ValueError:
            pass
    return infer_category(finding.rule_id)


def build_review_summary_note(
    *,
    review_id: UUID,
    findings: Sequence[Finding],
    has_blocker: bool,
    blocker_count: int,
    policy_applied: str | None,
    detail_url: str | None,
    engine_error: str | None = None,
    review_mode: str | None = None,
    incremental_base_sha: str | None = None,
    incremental_head_sha: str | None = None,
    new_finding_count: int | None = None,
    carried_finding_count: int | None = None,
    mode_reason: str | None = None,
    new_findings: Sequence[Finding] | None = None,
    carried_findings: Sequence[Finding] | None = None,
    skipped_files: Sequence[SkippedFileEntry] | None = None,
    reviewed_file_count: int | None = None,
    file_max_chars: int | None = None,
) -> str:
    """Render the top-level GitLab MR summary note for one review run.

    引擎失败时（``engine_error`` 非空）**顶部横幅**改成醒目的 ``# ⚠️ AI Review
    FAILED`` 标题 + 一条一句话说明，避免把 "PASSED / 0 findings" 装作正常
    完成——那是我们在
    `fix/main-llm-fail-error-and-config` 之前踩到的坑。

    增量审查引入后新增可选参数：
      - ``review_mode``: ``"full"`` / ``"incremental"`` / ``"reuse"``；
      - ``incremental_base_sha`` / ``incremental_head_sha``: 增量模式下用于
        在顶部提示 ``prev_head → new_head``；
      - ``new_finding_count`` / ``carried_finding_count``: 增量模式下分别是
        本次新增与历史遗留数量；
      - ``mode_reason``: 供 rebase 降级等场景在顶部加一句解释（如
        ``history_rewritten``）。

    v2 汇总（本次 PR）新增两组可选 findings 集合参数：
      - ``new_findings``: 本次 push 新引入的 findings（增量模式下由
        orchestrator 从 ``merge.new_findings`` 传入）；
      - ``carried_findings``: 未改动文件里遗留的历史 findings（对应
        ``merge.carried_over_untouched``）。

    传入这两组参数后，Findings 段落会拆分成两个二级标题：``🆕 本次新增`` 与
    ``📌 历史遗留``，并在每个分区内按 ``file_path`` 分组、按 severity 排序。
    两者都未传时保持向后兼容——所有 findings 都当作"新增"分区展示，等价于
    旧扁平列表的分组升级版。

    按文件粒度并发审查（feat/per-file-concurrent-review）新增跳过文件展示参数：
      - ``skipped_files``: 被跳过的文件清单（过大 / 审查失败），由 orchestrator
        从 ``engine.skipped_files`` 传入（``model_dump()`` 后的 dict 序列）；
      - ``reviewed_file_count``: 实际完成审查的文件数，用于覆盖说明开头；
      - ``file_max_chars``: 单文件 diff 阈值，用于“文件过大”分组标题展示。

    非空时会在脚注前追加 ``### 审查覆盖说明`` 段落，明确列出未覆盖的文件，
    避免用户误以为“全部审查通过”。为空时不展示（向后兼容）。

    这些字段全部可选，老调用点不传时行为完全等价旧输出。
    """

    if engine_error is not None:
        lines = [
            "# ⚠️ AI Review FAILED",
            "",
            "**本次 AI 审查未能完成，请人工审查这次 MR。**",
            "",
            f"- Review ID: `{review_id}`",
            f"- Result: {'BLOCKED' if has_blocker else 'FAILED (policy allowed merge)'}",
            "- Findings: 0 (engine did not run)",
            f"- Blocking findings: {blocker_count} blocking finding(s)",
        ]
        if policy_applied:
            lines.append(f"- Policy: `{policy_applied}`")
        if detail_url:
            lines.append(f"- Details: {detail_url}")
        if review_mode == "full" and mode_reason == "history_rewritten":
            # 引擎失败时也把 rebase 降级信息带出来，避免用户以为审查是老范围。
            lines.append("- ⚠️ 检测到 history 被改写，本次改为全量重审")
        lines.extend(
            [
                "",
                "### Engine error",
                "",
                engine_error,
                "",
                "_Generated by ai-code-reviewer._",
            ]
        )
        return "\n".join(lines)

    output: list[str] = []
    # 模式横幅：非默认 full 或者带 rebase 降级原因时才展示，减少信息噪声。
    banner = _build_mode_banner(
        review_mode=review_mode,
        incremental_base_sha=incremental_base_sha,
        incremental_head_sha=incremental_head_sha,
        new_finding_count=new_finding_count,
        carried_finding_count=carried_finding_count,
        mode_reason=mode_reason,
        total=len(findings),
    )
    if banner:
        output.extend([banner, ""])
    output.extend(
        [
            "## AI Review completed",
            "",
            f"- Review ID: `{review_id}`",
            f"- Result: {'BLOCKED' if has_blocker else 'PASSED'}",
            f"- Findings: {len(findings)} finding(s)",
        ]
    )
    # 分布行：只在 findings 非空时插入，且严格夹在 Findings 与 Blocking findings
    # 之间，方便 reviewer 一眼扫到重度/类别的构成。
    if findings:
        sev_dist = _compute_severity_distribution(findings)
        cat_dist = _compute_category_distribution(findings)
        if sev_dist:
            output.append(
                "  - " + " · ".join(
                    f"{emoji} {label}: {count}" for emoji, label, count in sev_dist
                )
            )
        if cat_dist:
            output.append(
                "  - " + " · ".join(
                    f"{emoji} {label}: {count}" for emoji, label, count in cat_dist
                )
            )
    output.append(f"- Blocking findings: {blocker_count} blocking finding(s)")
    if policy_applied:
        output.append(f"- Policy: `{policy_applied}`")
    if detail_url:
        output.append(f"- Details: {detail_url}")

    # v2 分区：优先看调用方是否提供了 new/carried；都没提供则视为向后兼容，
    # 把 findings 全部当新增分区展示。
    provided_split = new_findings is not None or carried_findings is not None
    if provided_split:
        new_list = list(new_findings or [])
        carried_list = list(carried_findings or [])
    else:
        new_list = list(findings)
        carried_list = []

    if not new_list and not carried_list:
        output.extend(["", "No findings were reported by the configured engine."])
    else:
        if new_list:
            output.extend(
                [
                    "",
                    f"### 🆕 本次新增（{len(new_list)} 条）",
                    "",
                ]
            )
            output.extend(_render_findings_section(new_list))
        if carried_list:
            output.extend(
                [
                    "",
                    f"### 📌 历史遗留（未改动文件，{len(carried_list)} 条）",
                    "",
                    "> 以下问题所在文件本次 push 未改动，保留自上一次审查。",
                    "",
                ]
            )
            output.extend(_render_findings_section(carried_list))
    if skipped_files:
        output.extend(
            _render_skipped_files_section(
                skipped_files=skipped_files,
                reviewed_file_count=reviewed_file_count,
                file_max_chars=file_max_chars,
            )
        )
    output.extend(["", "_Generated by ai-code-reviewer._"])
    return "\n".join(output)


def build_commit_review_note(
    *,
    review_id: UUID,
    commit_sha: str,
    commit_title: str,
    findings: Sequence[Finding],
    has_blocker: bool,
    blocker_count: int,
    policy_applied: str | None,
    detail_url: str | None,
    engine_error: str | None = None,
) -> str:
    """渲染单条 commit 审查的 GitLab 汇总评论。

    风格对齐 :func:`build_review_summary_note`（emoji 标题、severity/category
    分布、复用 ``_render_findings_section`` 渲染 findings 列表），但不带 MR
    专属字段（review_mode / incremental_base 等）。commit 审查没有"增量/
    复用"概念，只有"这一条 commit 相对其第一个 parent 的 diff"。

    Args:
        review_id: 评审记录 UUID。
        commit_sha: 被审查的 commit SHA。
        commit_title: commit 标题（首行）。
        findings: 引擎产出的 findings。
        has_blocker: 是否命中阻断策略。
        blocker_count: 阻断级 finding 数。
        policy_applied: 命中的阻断策略描述（如 ``"* -> NONE"``）。
        detail_url: 详情页链接；None 时省略。
        engine_error: 引擎失败原因摘要；非 None 时渲染"审查失败"文案，
            与 :func:`build_review_summary_note` 的 FAILED 横幅语义一致。

    Returns:
        Markdown 评论正文。
    """

    sha_short = commit_sha[:7] if commit_sha else "?"
    if engine_error is not None:
        lines = [
            "# ⚠️ AI Review FAILED",
            "",
            "**本次 commit 的 AI 审查未能完成，请人工审查这次提交。**",
            "",
            f"- Review ID: `{review_id}`",
            f"- Commit: `{sha_short}` {commit_title}".rstrip(),
            "- Findings: 0 (engine did not run)",
            "",
            "### Engine error",
            "",
            engine_error,
            "",
            "_Generated by ai-code-reviewer._",
        ]
        return "\n".join(lines)

    output: list[str] = [
        "## AI Commit Review completed",
        "",
        f"- Review ID: `{review_id}`",
        f"- Commit: `{sha_short}` {commit_title}".rstrip(),
        f"- Result: {'BLOCKED' if has_blocker else 'PASSED'}",
        f"- Findings: {len(findings)} finding(s)",
    ]
    if findings:
        sev_dist = _compute_severity_distribution(findings)
        cat_dist = _compute_category_distribution(findings)
        if sev_dist:
            output.append(
                "  - " + " · ".join(
                    f"{emoji} {label}: {count}" for emoji, label, count in sev_dist
                )
            )
        if cat_dist:
            output.append(
                "  - " + " · ".join(
                    f"{emoji} {label}: {count}" for emoji, label, count in cat_dist
                )
            )
    output.append(f"- Blocking findings: {blocker_count} blocking finding(s)")
    if policy_applied:
        output.append(f"- Policy: `{policy_applied}`")
    if detail_url:
        output.append(f"- Details: {detail_url}")

    if not findings:
        output.extend(
            [
                "",
                "无可审查变更：空提交或全部文件被忽略路径过滤，未发现问题。",
            ]
        )
    else:
        output.extend([""])
        output.extend(_render_findings_section(findings))
    output.extend(["", "_Generated by ai-code-reviewer._"])
    return "\n".join(output)


def build_finding_discussion_body(finding: Finding) -> str:
    """Render one line-level GitLab discussion body for a finding.

    模板结构（PR-A）：
      1. 首行：``{severity_emoji} **[{SEV}] · {cat_emoji} {cat_label}** — {title}``
      2. rule_id 行：``\\`{rule_id}\\```
      3. description（若有）
      4. Suggestion 块——按 existing_code / suggestion 组合分三种渲染：
         - 有 existing_code 或 suggestion 像代码：``<details>`` 折叠 + fenced block
         - 只有 suggestion 且是纯文字：一行 ``**建议**：...``
         - 什么都没有：跳过
      5. AI 声明脚注（永远出现，作为误报反馈入口）
    """

    sev_emoji, sev_label = severity_display(finding.severity)
    cat = _resolve_finding_category(finding)
    cat_emoji, cat_label = category_display(cat)

    lines: list[str] = [
        f"{sev_emoji} **[{sev_label}] · {cat_emoji} {cat_label}** — {finding.title}",
        "",
        f"`{finding.rule_id}`",
    ]
    if finding.description:
        lines.extend(["", finding.description])

    suggestion = finding.suggestion
    existing = finding.existing_code
    if existing or (suggestion and _suggestion_looks_like_code(suggestion)):
        # 走折叠对比：有 existing_code 就出「当前代码」块；有 suggestion 且是代码
        # 就出「建议改为」块。fenced block 不指定语言，让 GitLab 自动 detect。
        lines.extend(["", "<details><summary>💡 建议修复</summary>", ""])
        if existing:
            lines.extend(["**当前代码：**", "", "```", existing, "```", ""])
        if suggestion:
            if _suggestion_looks_like_code(suggestion):
                lines.extend(["**建议改为：**", "", "```", suggestion, "```", ""])
            else:
                # existing_code + 一行文字 suggestion 的混合情况：文字直接放进
                # details 里，避免 UI 出两个层级。
                lines.extend([f"**建议**：{suggestion}", ""])
        lines.append("</details>")
    elif suggestion:
        # 只有一行文字建议，不折叠，节省点击。
        lines.extend(["", f"**建议**：{suggestion}"])

    lines.extend(["", _AI_FOOTER])
    return "\n".join(lines)


# severity → 权重。同时兼容大小写与规范里提到的 blocker/critical/high/medium/low/info
# 命名；未识别的字符串一律视作 0，避免顺序抖动。
_SEVERITY_WEIGHTS: dict[str, int] = {
    "blocker": 4,
    "critical": 3,
    "high": 3,
    "warning": 2,
    "medium": 2,
    "low": 1,
    "info": 0,
}


def _severity_weight(severity: str | None) -> int:
    """归一化后查权重表。未知或 None 返回 0。"""

    if severity is None:
        return 0
    return _SEVERITY_WEIGHTS.get(severity.lower(), 0)


def _render_findings_section(findings: Sequence[Finding]) -> list[str]:
    """把一组 findings 按 ``file_path`` 分组、渲染为 markdown 行。

    文件排序：先按"文件内最高 severity 权重"降序，再按 finding 数量降序，
    最后按 file_path 字母序稳定兜底。文件内部按 ``line_number ASC``
    （``None`` 排到最后），再按 severity 权重降序。
    """

    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.file_path].append(finding)

    def _file_sort_key(item: tuple[str, list[Finding]]) -> tuple[int, int, str]:
        path, items = item
        max_weight = max((_severity_weight(f.severity) for f in items), default=0)
        # 负号让"越严重/越多"排前面；file_path 用字符串正向做字母序 tiebreak。
        return (-max_weight, -len(items), path)

    lines: list[str] = []
    for file_path, file_findings in sorted(grouped.items(), key=_file_sort_key):
        ordered = sorted(
            file_findings,
            key=lambda f: (
                # None 排最后：用 (has_line=0/1, line) 组合，None 变 (1, 0)。
                (0, f.line_number) if f.line_number is not None else (1, 0),
                -_severity_weight(f.severity),
            ),
        )
        lines.extend(
            [
                f"#### `{file_path}` （{len(ordered)} 条）",
                "",
            ]
        )
        for finding in ordered:
            severity_label = finding.severity.upper()
            sev_emoji, _ = severity_display(finding.severity)
            cat_emoji, _ = category_display(_resolve_finding_category(finding))
            lines.append(
                f"- {sev_emoji} {cat_emoji} **[{severity_label}] {finding.title}**"
            )
            if finding.line_number is not None:
                # 保留 file:line 完整定位——分组标题只有文件名，遗漏行号会让
                # 用户在长文件里找不着；同时兼容旧断言 ``file.py:LINE``。
                lines.append(f"  - Location: `{finding.file_path}:{finding.line_number}`")
            lines.append(f"  - Rule: `{finding.rule_id}`")
            if finding.description:
                lines.append(f"  - Description: {finding.description}")
            if finding.suggestion:
                lines.append(f"  - Suggestion: {finding.suggestion}")
        lines.append("")
    return lines


# 严重度分布展示时的固定顺序：从严到轻。
_SEVERITY_ORDER: tuple[str, ...] = ("BLOCKER", "WARNING", "INFO")

# 类别在 count 相同时的稳定次序：安全 > 缺陷 > 性能 > 可维护性 > 风格 > 其他。
_CATEGORY_ORDER: tuple[FindingCategory, ...] = (
    FindingCategory.SECURITY,
    FindingCategory.BUG,
    FindingCategory.PERFORMANCE,
    FindingCategory.MAINTAINABILITY,
    FindingCategory.STYLE,
    FindingCategory.OTHER,
)


def _compute_severity_distribution(
    findings: Sequence[Finding],
) -> list[tuple[str, str, int]]:
    """返回 [(emoji, label, count)]，按 BLOCKER/WARNING/INFO 固定顺序。

    count 为 0 的档次会被跳过，避免视觉噪声。
    """

    counter: dict[str, int] = defaultdict(int)
    for f in findings:
        counter[f.severity.upper()] += 1
    result: list[tuple[str, str, int]] = []
    for sev in _SEVERITY_ORDER:
        if counter.get(sev, 0) > 0:
            emoji, label = severity_display(sev)
            result.append((emoji, label, counter[sev]))
    return result


def _compute_category_distribution(
    findings: Sequence[Finding],
) -> list[tuple[str, str, int]]:
    """返回 [(emoji, label, count)]，按 count 降序 + 类别稳定序。

    count 为 0 的类别不出现。
    """

    counter: dict[FindingCategory, int] = defaultdict(int)
    for f in findings:
        counter[_resolve_finding_category(f)] += 1

    def _sort_key(item: tuple[FindingCategory, int]) -> tuple[int, int]:
        cat, count = item
        # count 降序 → 负号；同 count 时按 _CATEGORY_ORDER 稳定排。
        return (-count, _CATEGORY_ORDER.index(cat))

    result: list[tuple[str, str, int]] = []
    for cat, count in sorted(counter.items(), key=_sort_key):
        if count <= 0:
            continue
        emoji, label = category_display(cat)
        result.append((emoji, label, count))
    return result


def _humanize_skipped_detail(reason: str, detail: str | None) -> str:
    """把 too_large 的 ``"18432 chars"`` 之类 detail 人类可读化成 ``"18.4K chars"``。

    其他 reason 的 detail 原样返回（失败原因不适合压缩），``None`` / 空返回占位 ``-``。
    """

    if not detail:
        return "-"
    if reason == "too_large":
        match = re.match(r"^(\d+)\s*chars$", detail)
        if match:
            count = int(match.group(1))
            if count >= 1000:
                return f"{count / 1000:.1f}K chars"
    return detail


def _render_skipped_files_section(
    *,
    skipped_files: Sequence[SkippedFileEntry],
    reviewed_file_count: int | None,
    file_max_chars: int | None,
) -> list[str]:
    """渲染 ``### 审查覆盖说明`` 段落，列出被跳过的文件及原因（spec §5）。

    没有跳过文件时调用方不应调用本函数。返回的行列表末尾不带空行--脚注的
    空行由 ``build_review_summary_note`` 统一追加。
    """

    too_large = [s for s in skipped_files if s.get("reason") == "too_large"]
    failed = [s for s in skipped_files if s.get("reason") == "review_failed"]
    filtered_out = [
        s for s in skipped_files if s.get("reason") == "filtered_out"
    ]
    skipped_count = len(skipped_files)

    lines: list[str] = ["", "---", "", "### 审查覆盖说明", ""]
    reviewed_label = (
        str(reviewed_file_count) if reviewed_file_count is not None else "?"
    )
    lines.append(
        f"已审查 **{reviewed_label}** 个文件，跳过 **{skipped_count}** 个文件："
    )

    if too_large:
        threshold_label = f">{file_max_chars} chars" if file_max_chars else "过大"
        lines.extend(["", f"**文件过大（{threshold_label}）：** {len(too_large)} 个"])
        for s in too_large:
            path = s.get("file_path", "?")
            detail = _humanize_skipped_detail("too_large", s.get("detail"))
            lines.append(f"- `{path}` ({detail})")

    if failed:
        lines.extend(["", f"**审查失败：** {len(failed)} 个"])
        for s in failed:
            path = s.get("file_path", "?")
            detail = _humanize_skipped_detail("review_failed", s.get("detail"))
            lines.append(f"- `{path}` ({detail})")

    if filtered_out:
        lines.extend(["", f"**被过滤规则跳过：** {len(filtered_out)} 个"])
        for s in filtered_out:
            path = s.get("file_path", "?")
            lines.append(f"- `{path}`")

    return lines


def _build_mode_banner(
    *,
    review_mode: str | None,
    incremental_base_sha: str | None,
    incremental_head_sha: str | None,
    new_finding_count: int | None,
    carried_finding_count: int | None,
    mode_reason: str | None,
    total: int,
) -> str | None:
    """构造顶部一行模式横幅。返回 None 表示不展示（默认 full 模式）。

    incremental 模式：``ℹ️ 本次为增量审查（<base[:7]> → <head[:7]>），共 X 条
    （本次新增 A / 历史遗留 B）``
    reuse 模式：``ℹ️ CI 重跑，复用上一次审查结果``
    full + history_rewritten：``⚠️ 检测到 history 被改写，本次改为全量重审``
    """

    if review_mode == "incremental":
        base_short = (incremental_base_sha or "?")[:7]
        head_short = (incremental_head_sha or "?")[:7]
        parts = [
            "ℹ️ 本次为增量审查",
            f"（相较上一次 push 的 {base_short} → {head_short}）",
            f"，共 {total} 条问题",
        ]
        if new_finding_count is not None and carried_finding_count is not None:
            parts.append(
                f"（本次新增 {new_finding_count} / 历史遗留 {carried_finding_count}）",
            )
        return "".join(parts)
    if review_mode == "reuse":
        return "ℹ️ CI 重跑，复用上一次审查结果"
    if review_mode == "full" and mode_reason == "history_rewritten":
        return "⚠️ 检测到 history 被改写，本次改为全量重审"
    return None
