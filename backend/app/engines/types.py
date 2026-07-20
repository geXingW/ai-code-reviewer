"""Runtime data classes passed to and returned from review engines.

These models intentionally live separately from ``app.schemas`` (which
hold DB-row serialisation contracts). The runtime context an engine
receives during a code review is richer than what we persist:

* ``ReviewContext`` carries the diff, rule selection, provider settings
  and feedback history needed for one MR review.
* ``Finding`` is the engine's *output* — a structured suggestion that
  the orchestrator later persists into the ``findings`` table.
* ``HealthStatus`` is what ``ReviewEngine.health_check`` returns and the
  ``/api/engines/{name}/health`` endpoint surfaces.

Keeping these types decoupled from DB schemas lets future engines
(OcrEngine, StaticAnalyzerEngine, …) evolve their runtime shape without
forcing a migration.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["INFO", "WARNING", "BLOCKER"]
"""Severity levels mirrored from ``app.schemas.finding.Severity``."""

HealthState = Literal["ok", "degraded", "error"]
"""Operational state reported by :meth:`ReviewEngine.health_check`."""


class FindingSource(StrEnum):
    """Finding 的来源，Filter 阶段据此决定可否 drop。

    ``USER_RULE`` 表示这条 finding 命中了当前 ``ReviewContext.rules`` 中启用的
    团队/项目规则——是团队"就要报"的东西，Filter 只能因为不适用当前 diff 或
    严重度失衡而 downgrade，不允许因为"style 而已"之类的通用审美 drop。

    ``LANGUAGE_CHECKLIST`` 表示来自语言 checklist（``rule_docs/*.md``）的
    finding。目前 checklist 内容里没有能反向追溯的 rule_id 锚点，模型自己
    生成的 rule_id 无法回到 checklist——所以此枚举保留占位，engine 侧暂时
    **不会**主动打这个 tag（future work）。

    ``LLM_INFERRED`` 表示 LLM 自己发挥出来的 finding：没有明确的团队规则或
    checklist 背书。这是默认值，也是 Filter 阶段应该最激进证伪的一档。
    """

    USER_RULE = "user_rule"
    LANGUAGE_CHECKLIST = "language_checklist"
    LLM_INFERRED = "llm_inferred"


class DiffHunk(BaseModel):
    """A single contiguous diff hunk for one file.

    Attributes:
        file_path: Path of the file in the target branch.
        old_path: Path in the source branch (may differ on rename).
        new_start: First line number of the hunk in the new file.
        new_lines: Number of lines the hunk spans in the new file.
        old_start: First line number of the hunk in the old file.
        old_lines: Number of lines the hunk spans in the old file.
        content: Raw unified-diff content including ``+``/``-`` markers.
        is_new_file: True if this hunk is from a newly added file.
        is_deleted_file: True if this hunk is from a removed file.
    """

    model_config = ConfigDict(extra="forbid")

    file_path: str
    old_path: str | None = None
    new_start: int
    new_lines: int
    old_start: int
    old_lines: int
    content: str
    is_new_file: bool = False
    is_deleted_file: bool = False


class RuleSpec(BaseModel):
    """Rule projection delivered to engines.

    A ``RuleSpec`` is the *effective* rule applied to a review after
    per-project overrides have been resolved. The engine should not
    reach back into the DB — everything it needs is on this object.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    rule_id: str = Field(description="Stable human-readable rule key, e.g. 'no-print-prod'.")
    title: str
    description: str
    severity: Severity = Field(description="Severity after per-project override is applied.")
    category: str | None = None
    examples: list[str] = Field(default_factory=list)
    enabled: bool = True


class ProviderConfig(BaseModel):
    """LLM provider configuration handed to an engine.

    The orchestrator resolves secrets *before* invoking the engine so
    engines never touch the encrypted DB columns directly.
    """

    model_config = ConfigDict(extra="forbid")

    provider_id: UUID
    provider_type: str = Field(description="e.g. 'openai-compat', 'anthropic', 'gemini'.")
    base_url: str
    model: str
    api_key: str = Field(repr=False, description="Resolved plaintext API key (do not log).")
    temperature: float = 0.0
    max_tokens: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ReviewHistoryItem(BaseModel):
    """Historical false-positive feedback used to bias future reviews.

    The orchestrator feeds previously-confirmed false positives back
    into engines so they can learn to suppress similar findings.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    file_path: str
    line_number: int | None
    title: str
    description: str | None
    review_note: str | None
    confirmed_at: str = Field(description="ISO-8601 timestamp.")


class ReviewContext(BaseModel):
    """Everything an engine needs to review a single merge request.

    A ``ReviewContext`` is constructed once per review and is read-only
    from the engine's perspective.

    Attributes:
        review_id: ID of the persistent ``reviews`` row driving this run.
        project_id: ID of the project being reviewed.
        mr_iid: GitLab MR IID (human-facing ID, scoped to project).
        source_branch: Branch the MR is *from*.
        target_branch: Branch the MR is *into*.
        source_commit_sha: Head commit of the source branch.
        target_commit_sha: Base commit of the target branch.
        diff_hunks: All diff hunks comprising the MR.
        rules: Effective rules to evaluate.
        provider: Resolved LLM provider configuration.
        history: Prior confirmed false positives for this project.
        repo_url: Optional clone URL — only needed by engines that have
            ``ReviewEngine.requires_repo_clone() == True``.
        mr_title: MR 标题；作为 prompt 上下文注入，帮助模型理解意图。
        mr_description: MR 描述正文；同上，可能为空字符串。
        last_commit_message: MR head 分支最近一次 commit message；同上。
        extra: Open-ended bag for engine-specific extensions.
    """

    model_config = ConfigDict(extra="forbid")

    review_id: UUID
    project_id: UUID
    mr_iid: str
    source_branch: str
    target_branch: str
    source_commit_sha: str
    target_commit_sha: str
    diff_hunks: list[DiffHunk] = Field(default_factory=list)
    rules: list[RuleSpec] = Field(default_factory=list)
    provider: ProviderConfig | None = None
    history: list[ReviewHistoryItem] = Field(default_factory=list)
    repo_url: str | None = None
    mr_title: str = ""
    mr_description: str = ""
    last_commit_message: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    """Structured engine output for one issue in the reviewed diff.

    The orchestrator converts ``Finding`` instances into ``findings``
    DB rows. Field names align with :class:`app.schemas.finding.FindingCreate`
    so the conversion is a thin mapping, not a full re-shape.
    """

    model_config = ConfigDict(extra="forbid")

    file_path: str
    line_number: int | None = None
    rule_id: str
    severity: Severity
    title: str
    description: str | None = None
    suggestion: str | None = None
    existing_code: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # LLM 输出的分类（``security`` / ``bug`` / ``performance`` /
    # ``maintainability`` / ``style`` / ``other``）。保留 ``str | None`` 而不是
    # 收窄到 Literal——模型可能返回不在枚举内的值或缺省，渲染层会 fallback 到
    # rule_id 推断；这里的宽松让 ``Finding.model_validate_json`` 不因单条 finding
    # 的分类字段而拒收整个响应。
    category: str | None = None
    # 打上 finding 的来源标签，Filter 阶段据此分级处理：用户明确配的规则命中
    # 时默认保留，LLM 自己发挥出来的 finding 才走强对抗证伪。默认值刻意
    # 选最不受保护的 ``LLM_INFERRED``，避免调用方忘了打标签导致"误保护"。
    source: FindingSource = FindingSource.LLM_INFERRED


class HealthStatus(BaseModel):
    """Engine self-reported health.

    Returned by :meth:`ReviewEngine.health_check` and surfaced verbatim
    via the ``/api/engines/{name}/health`` REST endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    status: HealthState
    details: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
