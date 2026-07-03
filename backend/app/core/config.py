"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Annotated, cast

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the backend service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
    )

    app_name: Annotated[str, Field(description="Application name.")] = "ai-code-reviewer"
    app_version: Annotated[str, Field(description="Application semantic version.")] = "0.1.0-dev"
    debug: Annotated[bool, Field(description="Enable debug mode and colorful logs.")] = False
    database_url: Annotated[
        PostgresDsn,
        Field(description="PostgreSQL async connection URL."),
    ] = cast(
        PostgresDsn,
        "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
    )
    redis_url: Annotated[
        RedisDsn,
        Field(description="Redis connection URL."),
    ] = cast(RedisDsn, "redis://localhost:6379/0")
    secret_key: Annotated[
        SecretStr,
        Field(description="Fernet key used to encrypt tokens and secrets."),
    ] = SecretStr("CHANGE_ME_FERNET_KEY_GENERATE_WITH_Fernet.generate_key")
    log_level: Annotated[str, Field(description="Python logging level name.")] = "INFO"
    gitlab_base_url: Annotated[
        str,
        Field(description="GitLab instance base URL used by webhook review orchestration."),
    ] = "http://localhost"
    gitlab_token: Annotated[
        SecretStr,
        Field(description="GitLab access token used to read MR diff and write feedback."),
    ] = SecretStr("CHANGE_ME_GITLAB_TOKEN")
    gitlab_webhook_secret: Annotated[
        SecretStr,
        Field(description="Shared secret expected in the X-Gitlab-Token webhook header."),
    ] = SecretStr("test-webhook-secret")
    internal_api_token: Annotated[
        SecretStr,
        Field(description="Server-to-server token expected in the X-Internal-Token header."),
    ] = SecretStr("test-internal-token")
    admin_username: Annotated[
        str,
        Field(description="MVP single-account admin username for management API login."),
    ] = "admin"
    admin_password: Annotated[
        SecretStr,
        Field(description="MVP single-account admin password for management API login."),
    ] = SecretStr("admin")
    jwt_secret: Annotated[
        SecretStr,
        Field(description="Secret key used to sign standard JWT admin authentication tokens."),
    ] = SecretStr("CHANGE_ME_JWT_SECRET_GENERATE_32_PLUS_RANDOM_BYTES")
    jwt_algorithm: Annotated[
        str,
        Field(description="JWT signing algorithm (e.g. HS256, HS384, HS512)."),
    ] = "HS256"
    jwt_expires_in: Annotated[
        int,
        Field(description="JWT token lifetime in seconds."),
    ] = 86400
    default_review_engine: Annotated[
        str,
        Field(description="Default registered ReviewEngine name used for GitLab webhook reviews."),
    ] = "llm-direct"
    cors_origins: Annotated[
        list[str],
        Field(description="Allowed CORS origins."),
    ] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings.

    Returns:
        Settings: Parsed settings instance shared by the application.
    """

    return Settings()
