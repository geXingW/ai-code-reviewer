"""Review orchestration package: events, planning, engine execution, GitLab feedback.

Extracted from the former monolithic ``services/review_orchestrator.py`` (PR1 of the
orchestration split). Public symbols are re-exported here so callers can import
``services.review_orchestration`` directly; the old module path remains as a shim.
"""

from services.review_orchestration.diff_utils import (
    _is_line_number_valid_for_current_diff,  # tests/test_review_orchestrator.py 直接 import
)
from services.review_orchestration.events import (
    GitLabCommitEvent,
    GitLabMergeRequestEvent,
    GitLabPushEvent,
    _CommitLikeEvent,  # noqa: F401  包内共享
    _EventLike,  # noqa: F401
)
from services.review_orchestration.orchestrator import ReviewOrchestrator, SessionFactory
from services.review_orchestration.results import (
    CommitReviewResult,
    OrchestratorResult,
)

__all__ = [
    "CommitReviewResult",
    "GitLabCommitEvent",
    "GitLabMergeRequestEvent",
    "GitLabPushEvent",
    "OrchestratorResult",
    "ReviewOrchestrator",
    "SessionFactory",
    "_CommitLikeEvent",
    "_EventLike",
    "_is_line_number_valid_for_current_diff",
]
