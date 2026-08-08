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

    Args:
        project: The project whose ``gitlab_base_url`` and ``gitlab_access_token``
            will be used to construct the client.

    Returns:
        GitLabClient: A client instance bound to the project's GitLab instance.

    Raises:
        ValueError: If ``project.gitlab_base_url`` or ``project.gitlab_access_token``
            is empty.
    """

    if not project.gitlab_base_url:
        msg = "Project gitlab_base_url is empty; cannot build GitLab client."
        raise ValueError(msg)
    if not project.gitlab_access_token:
        msg = "Project gitlab_access_token is empty; cannot build GitLab client."
        raise ValueError(msg)
    return GitLabClient(
        base_url=project.gitlab_base_url,
        token=project.gitlab_access_token,
    )
