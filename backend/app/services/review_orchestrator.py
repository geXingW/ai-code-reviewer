"""Review orchestration from GitLab merge request events to engine execution.

This module intentionally keeps persistence optional for the MVP. It constructs the
runtime :class:`app.engines.types.ReviewContext`, runs the selected engine, writes
an aggregate MR note, and updates GitLab commit status. A later repository layer
can persist ``reviews`` / ``review_findings`` without changing this public flow.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.core.block_policy import (
    BlockPolicyLike,
    build_default_block_policies,
    compute_has_blocker,
    compute_has_blocker_for_engine_error,
    match_block_policy,
)
from app.core.diff_filter import DiffFilterConfig, filter_gitlab_changes
from app.core.summary_builder import (
    build_finding_discussion_body,
    build_review_summary_note,
)
from app.engines import DiffHunk, Finding, ReviewContext
from app.engines.registry import EngineRegistry, get_engine_registry
from app.integrations.gitlab.client import GitLabClient

_DIFF_HEADER_RE = re.compile(
    r"@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@",
)
logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class GitLabMergeRequestEvent:
    """Normalized GitLab merge request webhook event.

    Attributes:
        project_id: Numeric GitLab project ID.
        project_path: Namespace-qualified GitLab project path.
        mr_iid: Merge request IID scoped to the project.
        source_branch: Source branch name.
        target_branch: Target branch name.
        source_commit_sha: MR head commit SHA.
        target_commit_sha: Best-known base/default branch commit SHA.
        action: GitLab MR action, e.g. ``open`` or ``update``.
        title: Merge request title.
        web_url: Browser URL of the merge request.
    """

    project_id: int
    project_path: str
    mr_iid: int
    source_branch: str
    target_branch: str
    source_commit_sha: str
    target_commit_sha: str
    action: str
    title: str
    web_url: str | None = None

    @property
    def project_uuid(self) -> UUID:
        """Return a stable UUID projection for the GitLab project.

        The existing runtime engine contract expects UUID project IDs because the
        database model uses UUID primary keys. Until project lookup is wired in,
        deriving a UUID from the GitLab project ID keeps the context deterministic
        and avoids leaking integer IDs into the engine contract.
        """

        return uuid5(NAMESPACE_URL, f"gitlab-project:{self.project_id}")


@dataclass(frozen=True)
class OrchestratorResult:
    """Outcome returned after processing one merge request review."""

    review_id: UUID | None
    project_uuid: UUID
    status: str
    finding_count: int
    has_blocker: bool
    blocker_count: int = 0
    policy_applied: str | None = None
    note_id: int | None = None


class ReviewOrchestrator:
    """Coordinate GitLab diff retrieval, engine execution, and GitLab feedback."""

    def __init__(
        self,
        *,
        gitlab_client: GitLabClient,
        engine_registry: EngineRegistry | None = None,
        default_engine: str = "llm-direct",
        block_policies: Sequence[BlockPolicyLike] | None = None,
        ignore_paths: Sequence[str] | None = None,
        max_diff_bytes: int = 200_000,
        review_detail_base_url: str | None = None,
    ) -> None:
        self._gitlab_client = gitlab_client
        self._engine_registry = engine_registry or get_engine_registry()
        self._default_engine = default_engine
        self._block_policies = block_policies
        self._diff_filter_config = DiffFilterConfig(
            ignore_paths=tuple(ignore_paths or ()),
            max_diff_bytes=max_diff_bytes,
        )
        self._review_detail_base_url = (
            review_detail_base_url.rstrip("/") if review_detail_base_url else None
        )

    async def review_merge_request(self, event: GitLabMergeRequestEvent) -> OrchestratorResult:
        """Run the configured review engine for one GitLab MR event.

        Args:
            event: Normalized merge request event.

        Returns:
            OrchestratorResult: Aggregate execution summary.
        """

        review_id = uuid4()
        block_policy = match_block_policy(
            self._block_policies or build_default_block_policies(event.project_uuid),
            event.target_branch,
        )
        policy_applied = f"{block_policy.branch_pattern} -> {block_policy.block_severity}"
        changes = await self._gitlab_client.get_merge_request_changes(
            project_id=event.project_id,
            mr_iid=event.mr_iid,
        )
        context = ReviewContext(
            review_id=review_id,
            project_id=event.project_uuid,
            mr_iid=str(event.mr_iid),
            source_branch=event.source_branch,
            target_branch=event.target_branch,
            source_commit_sha=event.source_commit_sha,
            target_commit_sha=event.target_commit_sha,
            diff_hunks=self._build_diff_hunks(changes),
            extra={
                "gitlab_project_id": event.project_id,
                "gitlab_project_path": event.project_path,
                "merge_request_title": event.title,
                "merge_request_url": event.web_url,
                "merge_request_action": event.action,
            },
        )
        engine = self._engine_registry.get(self._default_engine)
        try:
            findings = await engine.review(context)
        except Exception as exc:
            logger.exception(
                "review engine failed",
                extra={
                    "project_id": event.project_id,
                    "mr_iid": event.mr_iid,
                    "engine": self._default_engine,
                },
            )
            return await self._handle_engine_error(
                event=event,
                review_id=review_id,
                policy_applied=policy_applied,
                block_policy=block_policy,
                error=exc,
            )
        has_blocker, blocker_count = compute_has_blocker(findings, block_policy)
        await self._post_finding_discussions(event, changes, findings)
        note = await self._gitlab_client.create_merge_request_note(
            project_id=event.project_id,
            mr_iid=event.mr_iid,
            body=build_review_summary_note(
                review_id=review_id,
                findings=findings,
                has_blocker=has_blocker,
                blocker_count=blocker_count,
                policy_applied=policy_applied,
                detail_url=self._build_review_detail_url(review_id),
            ),
        )
        await self._gitlab_client.set_commit_status(
            project_id=event.project_id,
            commit_sha=event.source_commit_sha,
            state="failed" if has_blocker else "success",
            name="ai-code-reviewer",
            description=(
                f"{len(findings)} finding(s), {blocker_count} blocking finding(s)"
                if has_blocker
                else f"AI Review completed with {len(findings)} finding(s)"
            ),
            target_url=self._build_review_detail_url(review_id),
        )
        return OrchestratorResult(
            review_id=review_id,
            project_uuid=event.project_uuid,
            status="done",
            finding_count=len(findings),
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            policy_applied=policy_applied,
            note_id=_extract_int(note, "id"),
        )

    def _build_diff_hunks(self, changes_payload: dict[str, Any]) -> list[DiffHunk]:
        """Convert GitLab ``changes`` payload into filtered engine diff hunks."""

        hunks: list[DiffHunk] = []
        raw_changes = changes_payload.get("changes", [])
        if not isinstance(raw_changes, list):
            return hunks
        for change in filter_gitlab_changes(raw_changes, self._diff_filter_config):
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

    async def _post_finding_discussions(
        self,
        event: GitLabMergeRequestEvent,
        changes_payload: dict[str, Any],
        findings: Sequence[Finding],
    ) -> None:
        """Post line-level GitLab discussions for findings with a valid location.

        Discussion creation is best-effort: a single stale line location should not
        prevent the summary note or commit status from being written back.
        """

        diff_refs = _extract_diff_refs(changes_payload, event)
        for finding in findings:
            if finding.line_number is None:
                continue
            old_path, new_path = _resolve_finding_paths(changes_payload, finding.file_path)
            try:
                await self._gitlab_client.create_merge_request_discussion(
                    project_id=event.project_id,
                    mr_iid=event.mr_iid,
                    body=build_finding_discussion_body(finding),
                    base_sha=diff_refs["base_sha"],
                    start_sha=diff_refs["start_sha"],
                    head_sha=diff_refs["head_sha"],
                    old_path=old_path,
                    new_path=new_path,
                    line_number=finding.line_number,
                )
            except Exception:
                logger.exception(
                    "failed to create GitLab MR discussion",
                    extra={
                        "project_id": event.project_id,
                        "mr_iid": event.mr_iid,
                        "file_path": finding.file_path,
                        "line_number": finding.line_number,
                    },
                )

    async def _handle_engine_error(
        self,
        *,
        event: GitLabMergeRequestEvent,
        review_id: UUID,
        policy_applied: str,
        block_policy: BlockPolicyLike,
        error: Exception,
    ) -> OrchestratorResult:
        """Persist deterministic GitLab feedback when the selected engine fails."""

        has_blocker, blocker_count = compute_has_blocker_for_engine_error(block_policy)
        note = await self._gitlab_client.create_merge_request_note(
            project_id=event.project_id,
            mr_iid=event.mr_iid,
            body=build_review_summary_note(
                review_id=review_id,
                findings=[],
                has_blocker=has_blocker,
                blocker_count=blocker_count,
                policy_applied=policy_applied,
                detail_url=self._build_review_detail_url(review_id),
                engine_error="AI Review engine failed before producing findings.",
            ),
        )
        await self._gitlab_client.set_commit_status(
            project_id=event.project_id,
            commit_sha=event.source_commit_sha,
            state="failed" if has_blocker else "success",
            name="ai-code-reviewer",
            description=(
                "AI Review engine failed and policy blocks merge"
                if has_blocker
                else "AI Review engine failed; policy allows merge"
            ),
            target_url=self._build_review_detail_url(review_id),
        )
        return OrchestratorResult(
            review_id=review_id,
            project_uuid=event.project_uuid,
            status="engine_error",
            finding_count=0,
            has_blocker=has_blocker,
            blocker_count=blocker_count,
            policy_applied=policy_applied,
            note_id=_extract_int(note, "id"),
        )

    def _build_review_detail_url(self, review_id: UUID) -> str | None:
        """Build an optional browser URL for the persisted review detail page."""

        if self._review_detail_base_url is None:
            return None
        return f"{self._review_detail_base_url}/reviews/{review_id}"


def _match_int(match: re.Match[str] | None, group: str, *, default: int) -> int:
    """Extract an int group from a regex match, returning ``default`` if absent."""

    if match is None:
        return default
    value = match.groupdict().get(group)
    if value is None:
        return default
    return int(value)


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
