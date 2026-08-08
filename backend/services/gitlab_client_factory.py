"""Factory for constructing GitLabClient instances from Project models.

All places that need a per-project GitLabClient should go through this factory
to ensure consistent construction (base URL from project, token from project,
validation of required fields).
"""

from __future__ import annotations

from integrations.gitlab.client import GitLabClient
from models.project import Project


def build_gitlab_client_for_project(project: Project) -> GitLabClient:
    """Build a GitLabClient configured from a Project record.

    ``project.gitlab_base_url`` 为空时自动兜底到全局 ``settings.gitlab_base_url``，
    兼容老数据（migration 0007 之前的项目 base_url 是空串）以及用户未主动配置
    自建 GitLab 地址的场景。

    Args:
        project: The project whose ``gitlab_base_url`` and ``gitlab_access_token``
            will be used to construct the client.

    Returns:
        GitLabClient: A client instance bound to the project's GitLab instance.

    Raises:
        ValueError: If ``project.gitlab_access_token`` is empty.
    """

    if not project.gitlab_access_token:
        msg = "Project gitlab_access_token is empty; cannot build GitLab client."
        raise ValueError(msg)

    base_url = project.gitlab_base_url
    if not base_url:
        from core.config import get_settings

        base_url = get_settings().gitlab_base_url

    return GitLabClient(
        base_url=base_url,
        token=project.gitlab_access_token,
    )
