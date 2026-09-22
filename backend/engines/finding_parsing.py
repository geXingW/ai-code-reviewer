"""Shared LLM-finding parsing and diff-localisation helpers.

从 ``engines.llm_engine.engine`` 抽出的公共解析层，供 ``llm-direct`` 与
``llm-agent`` 两个引擎复用，避免复制一份后行为漂移：

* 模型 JSON 容错解析（整体 fenced block 兼容）；
* raw finding 字典 -> ``Finding`` 运行时模型的规范化（行号回填、
  误报历史硬过滤、来源标签）；
* unified diff 的 added-line 定位工具（多 hunk 行号语义）。

纯函数集合：不持有状态、不访问网络与数据库。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from logging import getLogger
from typing import Any, cast

from pydantic import ValidationError

from engines.types import (
    DiffHunk,
    Finding,
    FindingSource,
    ReviewContext,
    ReviewHistoryItem,
)

logger = getLogger(__name__)

ALLOWED_SEVERITIES = {"INFO", "WARNING", "BLOCKER"}

# 与 review_orchestrator._DIFF_HEADER_RE 保持一致：Git 省略 ",1" 时计数组不出现。
_DIFF_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))?"
    r" \+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@",
    re.MULTILINE,
)

# 模型可能把整个 JSON 响应包进 ```json ... ```；只在整体解析失败且整个
# 响应被 fenced block 包裹时提取（suggestion 内嵌代码块的合法 JSON 不受影响）。
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)\s*```", re.DOTALL | re.IGNORECASE)


def loads_model_json(raw_response: str) -> dict[str, Any]:
    """Load model JSON, accepting an optional whole-response fenced block.

    先直接整体解析：json_object 模式下模型返回的就是纯 JSON，且 finding 的
    suggestion 字段可能内嵌 ```java 代码块，用 search 在任意位置提取会误伤
    （把内嵌代码块当成 JSON body，导致合法响应解析失败）。只有整体解析
    失败且整个响应被 ```json ... ``` 包裹时才提取 fenced body。
    """

    text = raw_response.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 整体不是合法 JSON：仅当整个响应是 fenced block 时提取，
        # 内嵌 ``` 的合法 JSON 不会走到这里（上面已成功解析）。
        match = _JSON_BLOCK_RE.fullmatch(text)
        if not match:
            raise
        data = json.loads(match.group("body").strip())
    if not isinstance(data, dict):
        msg = "LLM response must be a JSON object"
        raise ValueError(msg)
    return cast(dict[str, Any], data)


def tag_finding_source(finding: Finding, ctx: ReviewContext) -> Finding:
    """按 ``finding.rule_id`` 是否命中 ``ctx.rules`` 打来源标签。

    命中启用中的团队/项目规则 -> ``USER_RULE``（Filter 默认保留）。

    其余一律 ``LLM_INFERRED``（Filter 阶段最激进证伪的一档）。
    """

    user_rule_ids = {rule.rule_id for rule in ctx.rules if rule.enabled}
    if finding.rule_id in user_rule_ids:
        return finding.model_copy(update={"source": FindingSource.USER_RULE})
    return finding.model_copy(update={"source": FindingSource.LLM_INFERRED})


def file_in_diff(file_path: str, diff_hunks: list[DiffHunk]) -> bool:
    """Return True when ``file_path`` appears among the diff hunks."""

    return any(hunk.file_path == file_path for hunk in diff_hunks)


def line_in_diff(file_path: str, line_number: int, diff_hunks: list[DiffHunk]) -> bool:
    """Return True when ``line_number`` is an added line of ``file_path``."""

    return any(
        line_number in added_line_numbers(hunk)
        for hunk in diff_hunks
        if hunk.file_path == file_path
    )


def resolve_line_number(
    file_path: str,
    existing_code: str,
    diff_hunks: list[DiffHunk],
) -> int | None:
    """Match ``existing_code`` against added lines to recover the line number."""

    needle = " ".join(existing_code.strip().split())
    if not needle:
        return None
    for hunk in diff_hunks:
        if hunk.file_path != file_path:
            continue
        for line_no, code in iter_added_lines(hunk):
            haystack = " ".join(code.strip().split())
            if needle in haystack or haystack in needle:
                return line_no
    return None


def added_line_numbers(hunk: DiffHunk) -> set[int]:
    """Return the set of new-file line numbers of added lines in ``hunk``."""

    return {line_no for line_no, _ in iter_added_lines(hunk)}


def split_real_hunks(content: str) -> list[tuple[int, int, str]]:
    """Split a file's diff content into real hunks.

    一个 ``DiffHunk.content`` 可能包含多个 ``@@`` hunk。返回
    ``(new_start, new_lines, hunk_text)`` 列表；没有 @@ header 时返回空列表。
    """

    matches = list(_DIFF_HEADER_RE.finditer(content))
    result: list[tuple[int, int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        hunk_text = content[match.start() : end].strip("\n")
        new_lines_raw = match.group("new_lines")
        new_lines = int(new_lines_raw) if new_lines_raw else 1
        result.append((int(match.group("new_start")), new_lines, hunk_text))
    return result


def iter_added_lines(hunk: DiffHunk) -> list[tuple[int, str]]:
    """Return added lines with new-file line numbers for a unified diff hunk."""

    current_new_line = hunk.new_start
    added: list[tuple[int, str]] = []
    for raw_line in hunk.content.splitlines():
        if raw_line.startswith("@@"):
            # content 可能包含多个真实 hunk（一个 DiffHunk = 一个文件的全部 diff），
            # 遇到新的 @@ header 时必须重置行号计数器。
            header = _DIFF_HEADER_RE.search(raw_line)
            if header:
                current_new_line = int(header.group("new_start"))
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added.append((current_new_line, raw_line[1:]))
            current_new_line += 1
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        current_new_line += 1
    return added


def matches_false_positive_history(
    finding: Finding,
    history: list[ReviewHistoryItem],
) -> bool:
    """Return True when ``finding`` matches a confirmed false positive."""

    for item in history:
        if item.rule_id != finding.rule_id:
            continue
        if item.file_path != finding.file_path:
            continue
        if item.line_number is not None and finding.line_number is not None:
            if abs(item.line_number - finding.line_number) > 2:
                continue
        if item.title.strip().lower() == finding.title.strip().lower():
            return True
        if item.description and finding.description:
            if item.description.strip().lower() == finding.description.strip().lower():
                return True
    return False


def optional_str(value: object) -> str | None:
    """Normalise an arbitrary model value into a stripped string or None."""

    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value).strip() or None


def optional_int(value: object) -> int | None:
    """Normalise an arbitrary model value into a positive int or None."""

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if not isinstance(value, int | float | str | bytes | bytearray):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def clamp_confidence(value: object) -> float:
    """Clamp model-reported confidence into ``[0.0, 1.0]``."""

    try:
        confidence = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def normalise_raw_finding(
    raw: Mapping[str, Any],
    diff_hunks: list[DiffHunk],
) -> dict[str, Any] | None:
    """Normalise one raw model finding dict; None means "drop it".

    规则与 ``llm-direct`` 原实现一致：

    - ``file_path`` 必须出现在 diff 内；
    - ``severity`` 必须属于 :data:`ALLOWED_SEVERITIES`；
    - ``title`` / ``rule_id`` 必填；
    - 缺行号时用 ``existing_code`` 在 added lines 里回填；
    - 行号不在任何 added line 上时丢弃。
    """

    file_path = optional_str(raw.get("file_path"))
    if file_path is None or not file_in_diff(file_path, diff_hunks):
        return None

    severity = optional_str(raw.get("severity"))
    if severity not in ALLOWED_SEVERITIES:
        return None

    title = optional_str(raw.get("title"))
    rule_id = optional_str(raw.get("rule_id"))
    if not title or not rule_id:
        return None

    existing_code = optional_str(raw.get("existing_code"))
    line_number = optional_int(raw.get("line_number"))
    if line_number is None and existing_code:
        line_number = resolve_line_number(file_path, existing_code, diff_hunks)
    if line_number is not None and not line_in_diff(file_path, line_number, diff_hunks):
        return None

    return {
        "file_path": file_path,
        "line_number": line_number,
        "rule_id": rule_id,
        "severity": severity,
        "title": title,
        "description": optional_str(raw.get("description")),
        "suggestion": optional_str(raw.get("suggestion")),
        "existing_code": existing_code,
        # 模型偶尔会填 "null" 字符串或者空串--``optional_str`` 只把空/None
        # 归成 None，其它字符串一律原样透传。合法性交给渲染层收敛。
        "category": optional_str(raw.get("category")),
        "confidence": clamp_confidence(raw.get("confidence")),
    }


def parse_findings_from_response(
    raw_response: str,
    ctx: ReviewContext,
) -> list[Finding]:
    """Parse a model JSON response into validated, filtered findings.

    供 ``llm-direct`` / ``llm-agent`` 共用：解析 ``{"findings": [...]}``，
    逐条规范化 + 构造 :class:`Finding`，再做空误报历史过滤与来源标签。
    单条非法数据只跳过该条并打 INFO，不影响其余 finding。
    """

    payload = loads_model_json(raw_response)
    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        return []

    parsed: list[Finding] = []
    for raw in raw_findings:
        if not isinstance(raw, Mapping):
            continue
        normalized = normalise_raw_finding(raw, ctx.diff_hunks)
        if normalized is None:
            continue
        try:
            finding = Finding(**normalized)
        except ValidationError:
            logger.info(
                "ignored invalid finding payload",
                extra={"finding": normalized},
            )
            continue
        if matches_false_positive_history(finding, ctx.history):
            continue
        parsed.append(tag_finding_source(finding, ctx))
    return parsed


__all__ = [
    "ALLOWED_SEVERITIES",
    "added_line_numbers",
    "clamp_confidence",
    "file_in_diff",
    "iter_added_lines",
    "line_in_diff",
    "loads_model_json",
    "matches_false_positive_history",
    "normalise_raw_finding",
    "optional_int",
    "optional_str",
    "parse_findings_from_response",
    "resolve_line_number",
    "split_real_hunks",
    "tag_finding_source",
]
