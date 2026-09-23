"""Diff payload parsing helpers and finding projection utilities."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from core.diff_filter import DiffFilterConfig, filter_gitlab_changes
from core.finding_taxonomy import infer_category
from engines import DiffHunk, Finding
from models.finding import Finding as FindingRow
from services.review_orchestration.events import GitLabMergeRequestEvent

_DIFF_HEADER_RE = re.compile(
    r"@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@",
)

_SEVERITY_ORDER = ("BLOCKER", "WARNING", "INFO")


def build_diff_hunks(
    changes_payload: dict[str, Any],
    diff_filter_config: DiffFilterConfig,
) -> list[DiffHunk]:
    """Convert GitLab ``changes`` payload into filtered engine diff hunks."""

    hunks: list[DiffHunk] = []
    raw_changes = changes_payload.get("changes", [])
    if not isinstance(raw_changes, list):
        return hunks
    for change in filter_gitlab_changes(raw_changes, diff_filter_config):
        diff = str(change.get("diff") or "")
        header = _DIFF_HEADER_RE.search(diff)
        hunks.append(
            DiffHunk(
                file_path=str(change.get("new_path") or change.get("old_path") or "unknown"),
                old_path=str(change.get("old_path") or "") or None,
                new_start=_match_int(header, "new_start", default=1),
                new_lines=_match_int(header, "new_lines", default=1),
                old_start=_match_int(header, "old_start", default=1),
                old_lines=_match_int(header, "old_lines", default=1),
                content=diff,
                is_new_file=bool(change.get("new_file", False)),
                is_deleted_file=bool(change.get("deleted_file", False)),
            )
        )
    return hunks


def _match_int(match: re.Match[str] | None, group: str, *, default: int) -> int:
    """Extract an int group from a regex match, returning ``default`` if absent."""

    if match is None:
        return default
    value = match.groupdict().get(group)
    if value is None:
        return default
    return int(value)


def _build_findings_summary(findings: Sequence[Finding]) -> list[dict[str, Any]]:
    """把 finding 列表按严重级别分组，构造成通知正文用的精简摘要。

    每组形如 ``{"severity": "BLOCKER", "items": [{"title", "file_path",
    "line_number"}, ...]}``，按 BLOCKER -> WARNING -> INFO 固定顺序输出；
    空级别不生成组。截断（WARNING/INFO 各最多 5 条等）由通知服务在渲染时
    决定，这里只负责全量分组。
    """

    summary: list[dict[str, Any]] = []
    for severity in _SEVERITY_ORDER:
        items = [
            {
                "title": finding.title,
                "file_path": finding.file_path,
                "line_number": finding.line_number,
                "severity": finding.severity,
                # LLM 未输出分类时按 rule_id 推断，保证通知里「问题类型」始终有值。
                "category": finding.category or infer_category(finding.rule_id).value,
            }
            for finding in findings
            if finding.severity == severity
        ]
        if items:
            summary.append({"severity": severity, "items": items})
    return summary


def _extract_int(payload: dict[str, Any], key: str) -> int | None:
    """Extract an optional integer from a response payload."""

    value = payload.get(key)
    return value if isinstance(value, int) else None


def _extract_diff_refs(
    changes_payload: dict[str, Any],
    event: GitLabMergeRequestEvent,
) -> dict[str, str]:
    """Return GitLab diff refs, falling back to webhook SHAs when absent."""

    raw_refs = changes_payload.get("diff_refs")
    refs = raw_refs if isinstance(raw_refs, dict) else {}
    base_sha = str(refs.get("base_sha") or event.target_commit_sha)
    start_sha = str(refs.get("start_sha") or event.target_commit_sha)
    head_sha = str(refs.get("head_sha") or event.source_commit_sha)
    return {"base_sha": base_sha, "start_sha": start_sha, "head_sha": head_sha}


def _is_line_number_valid_for_current_diff(
    changes_payload: dict[str, Any],
    file_path: str,
    line_number: int,
) -> bool:
    """
    检查 line_number 是否在当前 diff 对应文件的有效范围内。

    MR 关闭后再 push 新代码、重开 MR 时，旧 finding 的 line_number 可能已经
    和最新 diff 对不上。此时强行贴到旧行号会显示错误的代码区域，甚至
    完全不显示。

    返回 True：行号在当前 diff 的某个 hunk 范围内，可以安全贴行级评论。
    返回 False：行号已失效，降级成全局 MR 备注。
    """
    for change in changes_payload.get("changes", []):
        change_new_path = change.get("new_path")
        if change_new_path != file_path:
            continue

        # 文件已删除，肯定不能贴行级评论
        if change.get("deleted_file"):
            return False

        diff = change.get("diff", "")

        # 空 diff → 无代码改动，拒绝行级评论
        if not diff:
            return False

        # 有 hunk 才校验，不可解析的 diff → 保守允许创建（无法证实行号失效）
        has_hunks = _DIFF_HEADER_RE.search(diff) is not None
        if not has_hunks:
            return True

        # 解析 diff header，检查行号是否在某个 hunk 的 new_line 范围内
        for match in _DIFF_HEADER_RE.finditer(diff):
            new_start = int(match.group("new_start"))
            new_lines = int(match.group("new_lines") or 1)
            new_end = new_start + new_lines - 1

            if new_start <= line_number <= new_end:
                return True

        # 有 hunk 但行号不在范围内 → 真正失效了，降级成全局评论
        return False

    # 文件不在本次 diff 里 → 降级
    return False


def _resolve_finding_paths(changes_payload: dict[str, Any], file_path: str) -> tuple[str, str]:
    """Resolve old/new diff paths for a finding path from GitLab changes."""

    raw_changes = changes_payload.get("changes", [])
    if isinstance(raw_changes, list):
        for item in raw_changes:
            if not isinstance(item, dict):
                continue
            old_path = str(item.get("old_path") or "")
            new_path = str(item.get("new_path") or "")
            if file_path in {old_path, new_path}:
                return old_path or file_path, new_path or file_path
    return file_path, file_path


def _finding_row_to_engine(row: FindingRow) -> Finding:
    """把 DB 行投影回 engine.Finding，供合并展示与 reuse 复用。

    这里的 Finding 只用于 note / discussion 渲染，因此 ``source`` 用默认值
    （无法回溯规则来源），``existing_code`` / ``suggestion`` 保留原样。
    """

    severity = row.severity if row.severity in ("INFO", "WARNING", "BLOCKER") else "WARNING"
    return Finding(
        file_path=row.file_path,
        line_number=row.line_number,
        rule_id=row.rule_id,
        severity=severity,
        title=row.title,
        description=row.description,
        suggestion=row.suggestion,
        existing_code=row.existing_code,
        confidence=float(row.confidence or 0.0),
    )
