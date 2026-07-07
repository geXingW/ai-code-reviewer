"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Annotated, cast

from cryptography.fernet import Fernet
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


# 占位符集合：出现这些值说明 SECRET_KEY 仍是模板默认值，并未真正配置。
_SECRET_KEY_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "CHANGE_ME_GENERATE_A_REAL_FERNET_KEY",
        "CHANGE_ME_FERNET_KEY_GENERATE_WITH_Fernet.generate_key",
    },
)


def validate_secret_key(settings: Settings) -> None:
    """启动期 fail-fast 校验 SECRET_KEY，避免加密字段在运行时才报错。

    校验逻辑参考 ``app/models/encryption.py::_get_fernet``，只是前置到应用启动阶段：
    provider.api_key / project.gitlab_access_token / project.webhook_secret 都依赖该密钥，
    若缺失或非法应在启动时立即拒绝，而不是等到首次写入加密字段时才暴露。

    Args:
        settings: 已加载的应用配置。

    Raises:
        RuntimeError: SECRET_KEY 为空、仍是占位符，或不是合法 Fernet 密钥。
    """

    secret_key = settings.secret_key.get_secret_value()
    if not secret_key or secret_key in _SECRET_KEY_PLACEHOLDERS:
        msg = (
            "SECRET_KEY 未配置或仍为占位符。请用 "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())" 生成真实 Fernet 密钥并设置到环境变量。'
        )
        raise RuntimeError(msg)
    try:
        Fernet(secret_key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        msg = "SECRET_KEY 不是合法的 Fernet 密钥，请用 Fernet.generate_key() 重新生成。"
        raise RuntimeError(msg) from exc
