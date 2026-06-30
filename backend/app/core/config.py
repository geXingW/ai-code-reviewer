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
    cors_origins: Annotated[
        list[str],
        Field(description="Allowed browser origins for CORS."),
    ] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings.

    Returns:
        Settings: Parsed settings instance shared by the application.
    """

    return Settings()
