"""Tests for project-level GitLab webhook client factory."""

from __future__ import annotations

import pytest

from models.project import Project
from services.gitlab_client_factory import build_gitlab_client_for_project


def test_build_gitlab_client_for_project_uses_project_fields() -> None:
    """``build_gitlab_client_for_project`` uses project base URL and token."""

    project = Project(
        name="test",
        gitlab_project_id="123",
        gitlab_base_url="https://gitlab.example.com",
        gitlab_access_token="glpat-test-token",
        webhook_secret="secret",
    )
    client = build_gitlab_client_for_project(project)

    assert client._base_url == "https://gitlab.example.com"
    assert client._token == "glpat-test-token"


def test_build_gitlab_client_for_project_falls_back_to_global_base_url() -> None:
    """Empty ``gitlab_base_url`` falls back to global ``settings.gitlab_base_url``.

    兼容 migration 0007 之前的老项目（base_url 默认为空串）。
    """

    project = Project(
        name="test",
        gitlab_project_id="123",
        gitlab_base_url="",
        gitlab_access_token="glpat-test",
        webhook_secret="secret",
    )
    client = build_gitlab_client_for_project(project)

    assert client._base_url == "https://gitlab.com"
    assert client._token == "glpat-test"


def test_build_gitlab_client_for_project_raises_on_empty_token() -> None:
    """Empty ``gitlab_access_token`` raises ValueError."""

    project = Project(
        name="test",
        gitlab_project_id="123",
        gitlab_base_url="https://gitlab.example.com",
        gitlab_access_token="",
        webhook_secret="secret",
    )
    with pytest.raises(ValueError, match="gitlab_access_token"):
        build_gitlab_client_for_project(project)
