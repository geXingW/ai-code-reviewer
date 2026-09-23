"""Normalized GitLab webhook event dataclasses shared by the orchestration package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5


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
        last_commit_message: MR head 分支最近一次 commit 的 message；来自
            ``object_attributes.last_commit.message``，可能为空（供 LLM 上下文使用）。
        created_at: MR 创建时间（ISO 字符串）；来自 ``object_attributes.created_at``，
            可能为空。
        author_username: MR 创建人（open 事件）/ 触发者的 GitLab 用户名；来自
            webhook 顶层 ``user.username``，缺失时为 ``None``（通知侧不 @ 人）。
        author_name: 同上，取 ``user.name`` 显示名。
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
    description: str = ""
    last_commit_message: str = ""
    created_at: str = ""
    author_username: str | None = None
    author_name: str | None = None

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
class GitLabCommitEvent:
    """Push Hook 逐 commit 审查用的归一化 commit 事件。

    一个 push payload 携带多个 commit，webhook 层为每个（截断后的）commit
    构造一个本事件交给 :meth:`ReviewOrchestrator.review_commit`。

    Attributes:
        project_id: 数值型 GitLab 项目 ID。
        project_path: 带命名空间的项目路径。
        commit_sha: 该 commit 的 SHA。
        branch: push 目标分支名（``ref`` 去掉 ``refs/heads/`` 前缀）。
        title: commit 标题（首行）。
        message: 完整 commit message。
        author_username: push 触发者的 GitLab 用户名；缺失时 ``None``。
        author_name: 同上，显示名。
        created_at: commit 时间（ISO 或 Ruby ``to_s`` 字符串，webhook
            ``commit.timestamp`` 原样透传）；缺失时 ``""``，通知侧不展示。
    """

    project_id: int
    project_path: str
    commit_sha: str
    branch: str
    title: str
    message: str
    author_username: str | None = None
    author_name: str | None = None
    created_at: str = ""

    @property
    def project_uuid(self) -> UUID:
        """与 :class:`GitLabMergeRequestEvent` 相同的 uuid5 派生，保证同一
        GitLab 项目在 MR / commit / push 三条审查链路里映射到同一个内部 UUID。"""

        return uuid5(NAMESPACE_URL, f"gitlab-project:{self.project_id}")


@dataclass(frozen=True)
class GitLabPushEvent:
    """Push Hook 合并审查用的归一化 push 事件。

    一次 push 携带的全部 commit 变更合并后做**单次** LLM 审查，行为规则见
    :meth:`ReviewOrchestrator.review_push`。

    Attributes:
        project_id: 数值型 GitLab 项目 ID。
        project_path: 带命名空间的项目路径。
        branch: push 目标分支名（``ref`` 去掉 ``refs/heads/`` 前缀）。
        before_sha: push 前 ref 指向的 commit SHA（新建分支时为 40 个 0）。
        after_sha: push 后 ref 指向的 commit SHA（head commit）。
        commits: ``[{"id": sha, "title": ..., "message": ...}, ...]``，
            保持 payload 的时间序（旧 -> 新）。
        author_username: push 触发者的 GitLab 用户名；缺失时 ``None``。
        author_name: 同上，显示名。
        created_at: 本次 push 的代表时间（head commit 的 ``timestamp`` 原样
            透传）；缺失时 ``""``，通知侧不展示。
    """

    project_id: int
    project_path: str
    branch: str
    before_sha: str
    after_sha: str
    commits: list[dict[str, Any]]
    author_username: str | None = None
    author_name: str | None = None
    created_at: str = ""

    @property
    def project_uuid(self) -> UUID:
        """与 :class:`GitLabMergeRequestEvent` 相同的 uuid5 派生，保证同一
        GitLab 项目在 MR / commit / push 三条审查链路里映射到同一个内部 UUID。"""

        return uuid5(NAMESPACE_URL, f"gitlab-project:{self.project_id}")

    @property
    def commit_sha(self) -> str:
        """head commit SHA（即 after_sha），合并审查的行级评论 / status 写回目标。

        与 :class:`GitLabCommitEvent.commit_sha` 对齐，让评论、失败反馈、通知
        三个复用方法能用同一个 duck-typing 字段。"""

        return self.after_sha

    @property
    def title(self) -> str:
        """head commit 标题（commits 列表最后一跳，message 首行）。

        供失败反馈与通知兜底展示；commits 为空时返回空串。"""

        for commit in reversed(self.commits):
            title = str(commit.get("title") or "").strip()
            if title:
                return title
        return ""


# provider / rules / history 三个 resolve helper 只依赖 event.project_id，
# MR / commit / push 事件 duck-typing 共用。
_EventLike = GitLabMergeRequestEvent | GitLabCommitEvent | GitLabPushEvent

# commit 级写回（评论 / status / 通知）复用的最小事件形状：都需要
# ``commit_sha``（写回目标）与 ``title``（展示用）两个字段。
_CommitLikeEvent = GitLabCommitEvent | GitLabPushEvent
