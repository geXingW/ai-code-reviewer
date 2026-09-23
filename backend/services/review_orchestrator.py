"""DEPRECATED shim：内容已迁移到 services.review_orchestration 包。

保留此模块仅为兼容既有 import 路径（api/gitlab_webhook.py、api/reviews.py、
tests/*）。新代码请直接 import services.review_orchestration。
"""

from core.config import (
    get_settings as get_settings,  # noqa: F401  兼容 tests 对本模块 get_settings 的 monkeypatch
)
from services.review_orchestration import (  # noqa: F401
    CommitReviewResult as CommitReviewResult,
)
from services.review_orchestration import (
    GitLabCommitEvent as GitLabCommitEvent,
)
from services.review_orchestration import (
    GitLabMergeRequestEvent as GitLabMergeRequestEvent,
)
from services.review_orchestration import (
    GitLabPushEvent as GitLabPushEvent,
)
from services.review_orchestration import (
    OrchestratorResult as OrchestratorResult,
)
from services.review_orchestration import (
    ReviewOrchestrator as ReviewOrchestrator,
)
from services.review_orchestration import (
    SessionFactory as SessionFactory,
)
from services.review_orchestration.diff_utils import (
    _is_line_number_valid_for_current_diff as _is_line_number_valid_for_current_diff,  # noqa: F401
)
