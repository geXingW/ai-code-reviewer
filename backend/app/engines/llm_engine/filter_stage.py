"""证伪式后置过滤阶段（Filter stage）。

引擎跑完主 LLM 之后，把候选 findings 再交给一次 LLM 调用，让它对每条
finding 做“证伪 / 保留 / 降级”决定。设计原则：

- **Fail-open**：过滤链路任何环节出错（LLM 抛错、返回非法 JSON、开关关闭），
  必须原样返回输入 findings，绝不影响主流程；只输出 WARNING 日志。
- **保序**：keep 的 finding 顺序与输入一致。
- 未在 decisions 里出现的 finding 默认 keep。
- 非法 index / verdict / severity 单条忽略而不是整个过滤失败。
- **Defense in depth**：``source=USER_RULE`` 的 finding 由 prompt 侧要求默认
  保留；即便 LLM 违反 prompt 判 drop，``apply_decisions`` 里也代码兜底
  强制转 keep 并打 WARNING 日志，避免用户明确配的规则被无声吞掉。

模块级函数刻意做成纯函数（``_format_candidates`` / ``_apply_decisions`` /
``_parse_filter_response``）方便直接单测。``filter_findings`` 才做 IO。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from app.engines.types import Finding, FindingSource, ReviewContext, Severity

logger = logging.getLogger(__name__)

_ALLOWED_VERDICTS: frozenset[str] = frozenset({"keep", "drop", "downgrade"})
_ALLOWED_SEVERITIES: frozenset[str] = frozenset({"INFO", "WARNING", "BLOCKER"})


FilterVerdict = Literal["keep", "drop", "downgrade"]


@dataclass(frozen=True)
class FilterDecision:
    """单条 finding 的过滤决定，保留下来用于日志/审计。

    Attributes:
        index: 原始 findings 列表中的 0-based 位置。
        verdict: keep / drop / downgrade 三选一。
        reason: LLM 给出的判断理由（可能为空字符串）。
        new_severity: 仅 downgrade 时使用；keep / drop 时应为 None。
    """

    index: int
    verdict: FilterVerdict
    reason: str
    new_severity: Severity | None


def format_candidates(findings: list[Finding]) -> str:
    """把 findings 渲染成 filter user prompt 里的 candidate_findings_block。

    每条 finding 一段，包含 index / rule_id / severity / source / file:line /
    title / description / existing_code / suggestion，方便 LLM 精确定位并
    引用；index 是 keep/drop/downgrade 决定的锚点，source 让 LLM 知道这条
    finding 是不是用户明确配的团队规则。
    """

    if not findings:
        return "（无候选 finding）"

    blocks: list[str] = []
    for idx, finding in enumerate(findings):
        location = finding.file_path
        if finding.line_number is not None:
            location = f"{finding.file_path}:{finding.line_number}"
        header = (
            f"### [{idx}] rule_id={finding.rule_id} "
            f"severity={finding.severity} source={finding.source.value} "
            f"file={location}"
        )
        blocks.append(
            "\n".join(
                [
                    header,
                    f"title: {finding.title}",
                    f"description: {finding.description or ''}",
                    f"existing_code: {finding.existing_code or ''}",
                    f"suggestion: {finding.suggestion or ''}",
                ]
            )
        )
    return "\n\n".join(blocks)


def parse_filter_response(raw_json: str, findings_count: int) -> list[FilterDecision]:
    """解析 filter LLM 返回的 JSON，非法内容单条忽略。

    - JSON parse 失败 / 顶层不是 object / 缺 ``decisions`` 键 → 返回 ``[]``
      （fail-open：外层照原样保留 findings）。
    - 单条 decision 缺字段 / index 越界 / verdict 不在白名单 → 跳过该条。
    - downgrade 但 new_severity 缺失或非法 → 跳过该条（避免把 severity
      设成 None）。
    """

    if not raw_json or not raw_json.strip():
        return []

    try:
        payload = json.loads(raw_json.strip())
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []

    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        return []

    decisions: list[FilterDecision] = []
    for raw in raw_decisions:
        if not isinstance(raw, dict):
            continue

        index = raw.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        if index < 0 or index >= findings_count:
            continue

        verdict = raw.get("verdict")
        if not isinstance(verdict, str) or verdict not in _ALLOWED_VERDICTS:
            continue

        reason_raw = raw.get("reason", "")
        reason = reason_raw if isinstance(reason_raw, str) else ""

        new_severity_raw = raw.get("new_severity")
        new_severity: Severity | None
        if verdict == "downgrade":
            if not isinstance(new_severity_raw, str):
                continue
            if new_severity_raw not in _ALLOWED_SEVERITIES:
                continue
            # cast is safe: value is in _ALLOWED_SEVERITIES
            new_severity = new_severity_raw  # type: ignore[assignment]
        else:
            new_severity = None

        # cast is safe: verdict is in _ALLOWED_VERDICTS
        decisions.append(
            FilterDecision(
                index=index,
                verdict=verdict,  # type: ignore[arg-type]
                reason=reason,
                new_severity=new_severity,
            )
        )
    return decisions


def apply_decisions(
    findings: list[Finding],
    decisions: list[FilterDecision],
) -> list[Finding]:
    """按 decisions 处理 findings，返回保留后的列表（保持原顺序）。

    - decisions 空 → 原样返回。
    - 同一 index 多条 decision 只取最后一条（简单 last-write-wins，
      规避 LLM 偶发重复输出）。
    - drop → 丢弃；downgrade → 复制并替换 severity；keep / 未出现 → 保留。
    - **Defense in depth**：``source=USER_RULE`` 的 finding **永远保留**。
      即便 LLM 违反 prompt 判 drop，也强制转为 keep 并打 WARNING 日志，
      便于观察 LLM 违规频率——用户明确配的团队规则不允许被 Filter 用
      通用审美吞掉。downgrade 对 user_rule 仍允许（用于 severity 失衡校正）。
    """

    if not decisions:
        return list(findings)

    by_index: dict[int, FilterDecision] = {}
    for decision in decisions:
        if 0 <= decision.index < len(findings):
            by_index[decision.index] = decision

    kept: list[Finding] = []
    for idx, finding in enumerate(findings):
        matched = by_index.get(idx)
        if matched is None or matched.verdict == "keep":
            kept.append(finding)
            continue
        if matched.verdict == "drop":
            # user_rule 兜底：LLM 违反 prompt 想 drop 用户规则时强制保留。
            if finding.source == FindingSource.USER_RULE:
                logger.warning(
                    "filter stage: LLM tried to drop user_rule finding "
                    "rule_id=%s file=%s:%s title=%r reason=%r; forcing keep",
                    finding.rule_id,
                    finding.file_path,
                    finding.line_number,
                    finding.title,
                    matched.reason,
                )
                kept.append(finding)
                continue
            continue
        # downgrade
        if matched.new_severity is None:
            # 理论上 parse 阶段已排除，这里防御性走 keep 分支。
            kept.append(finding)
            continue
        kept.append(finding.model_copy(update={"severity": matched.new_severity}))
    return kept


def summarize_decisions(
    findings: list[Finding],
    decisions: list[FilterDecision],
) -> tuple[int, int, int, int, int]:
    """返回 (kept_touched, dropped, downgraded, user_rule_kept,
    user_rule_drop_attempts_blocked) 计数，仅用于日志。

    ``kept_touched`` 指 verdict=keep 明确出现在 decisions 里的条目数；
    未出现在 decisions 里的隐式 keep 不计入。

    ``user_rule_kept``：命中 ``FindingSource.USER_RULE`` 且最终保留下来
    的 finding 数（含"未被 decision 触及"和"decision keep/downgrade"两种
    情况）。

    ``user_rule_drop_attempts_blocked``：LLM 试图 drop 但被 apply_decisions
    兜底强制保留的 user_rule finding 条数——用来观察 LLM 违反 prompt
    的频率。
    """

    kept_touched = 0
    dropped = 0
    downgraded = 0
    for decision in decisions:
        if decision.verdict == "keep":
            kept_touched += 1
        elif decision.verdict == "drop":
            dropped += 1
        elif decision.verdict == "downgrade":
            downgraded += 1

    by_index: dict[int, FilterDecision] = {}
    for decision in decisions:
        if 0 <= decision.index < len(findings):
            by_index[decision.index] = decision

    user_rule_kept = 0
    user_rule_drop_blocked = 0
    for idx, finding in enumerate(findings):
        if finding.source != FindingSource.USER_RULE:
            continue
        matched = by_index.get(idx)
        if matched is None or matched.verdict in ("keep", "downgrade"):
            user_rule_kept += 1
        elif matched.verdict == "drop":
            # apply_decisions 会强制 keep，这里同步计数。
            user_rule_kept += 1
            user_rule_drop_blocked += 1
    return (
        kept_touched,
        dropped,
        downgraded,
        user_rule_kept,
        user_rule_drop_blocked,
    )


def format_filter_user_prompt(
    template: str,
    context: ReviewContext,
    candidate_findings_block: str,
    diff_block: str,
) -> str:
    """把 filter_user.md 模板里的占位符替换掉；模板不来自用户输入，直接
    ``str.replace`` 就够用，避免引入模板引擎。"""

    values = {
        "mr_title": context.mr_title or "（无标题）",
        "mr_description": context.mr_description or "（无描述）",
        "source_branch": context.source_branch,
        "target_branch": context.target_branch,
        "diff_block": diff_block,
        "candidate_findings_block": candidate_findings_block,
    }
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


__all__ = [
    "FilterDecision",
    "FilterVerdict",
    "apply_decisions",
    "format_candidates",
    "format_filter_user_prompt",
    "parse_filter_response",
    "summarize_decisions",
]
