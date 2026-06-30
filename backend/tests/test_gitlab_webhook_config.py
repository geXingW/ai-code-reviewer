"""Configuration tests for GitLab webhook settings."""

from app.core.config import Settings


def test_gitlab_settings_are_available() -> None:
    """Settings expose GitLab base URL/token/webhook secret for runtime wiring."""

    settings = Settings(
        database_url="postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/test_db",
        redis_url="redis://localhost:6379/0",
        secret_key="x" * 32,
        gitlab_base_url="https://gitlab.example.com",
        gitlab_token="glpat-test",
        gitlab_webhook_secret="test-webhook-secret",
    )

    assert settings.gitlab_base_url == "https://gitlab.example.com"
    assert settings.gitlab_token.get_secret_value() == "glpat-test"
    assert settings.gitlab_webhook_secret.get_secret_value() == "test-webhook-secret"
