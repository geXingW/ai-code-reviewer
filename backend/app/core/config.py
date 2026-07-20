"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Annotated, Literal, cast

from cryptography.fernet import Fernet
from pydantic import Field, RedisDsn, SecretStr
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
    # SQLAlchemy 异步连接 URL：默认 PostgreSQL，切换 DATABASE_URL 即可改用 MySQL（mysql+aiomysql://）。
    # 用 str 而非 PostgresDsn，避免 Pydantic 拒绝带驱动的 MySQL 方案。
    database_url: Annotated[
        str,
        Field(description="数据库异步连接 URL，支持 PostgreSQL 与 MySQL。"),
    ] = "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer"
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
    llm_filter_enabled: Annotated[
        bool,
        Field(
            description=(
                "是否启用 LLM 证伪式后置过滤阶段：True 表示对主 LLM 产出的 findings "
                "再走一次 LLM 决定 keep / drop / downgrade；False 直接返回原始 findings。"
                "默认 False：Filter 每次多一次 LLM 调用，延迟/成本翻倍，除非用户主动开启。"
            ),
        ),
    ] = False
    llm_request_timeout_seconds: Annotated[
        float,
        Field(
            gt=0.0,
            description=(
                "单次 LLM 请求超时（秒）。默认 180s 面向 reasoning 模型的常见首字节延迟；"
                "调用方可通过环境变量 LLM_REQUEST_TIMEOUT_SECONDS 覆盖。"
            ),
        ),
    ] = 180.0
    llm_max_retries: Annotated[
        int,
        Field(
            ge=0,
            description=(
                "LLM provider 请求的最大重试次数（不含首次调用）。"
                "默认 1：保留一次网络抖动兜底，避免总耗时爆炸。"
            ),
        ),
    ] = 1
    llm_prompt_max_chars: Annotated[
        int,
        Field(
            gt=0,
            description=(
                "user prompt 允许的最大字符数；超限时在 _build_prompt 中优先截断 diff "
                "段落。默认 32000 ≈ DeepSeek 64K context window 的保守 1/4。"
            ),
        ),
    ] = 32000
    # PR-B2：负例反哺 prompt 的两项开关。0 表示彻底禁用负例注入，让 context.history
    # 保持空；> 0 时按 scope 从 negative_examples 表拉批准过的负例。
    llm_history_max_items: Annotated[
        int,
        Field(
            ge=0,
            le=100,
            description=(
                "从 negative_examples 表向 ReviewContext.history 注入的最大条数。"
                "0 表示禁用负例反哺，engine 侧的历史段落将保持空。范围 [0, 100]。"
            ),
        ),
    ] = 20
    llm_history_scope: Annotated[
        Literal["project", "rule", "both"],
        Field(
            description=(
                "负例反哺的圈选范围。"
                "'project' 只拉当前项目的负例；"
                "'rule' 只拉当前启用规则命中的负例（含全局负例 project_id=NULL）；"
                "'both' 取并集，按 id 去重。默认 'both'。"
            ),
        ),
    ] = "both"
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
