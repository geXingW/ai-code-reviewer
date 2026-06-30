"""Redis client and health check utilities."""

from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

if TYPE_CHECKING:
    # types-redis 4.x stubs declare Redis as generic, but runtime (redis>=5)
    # does not support subscripting. Use Any in stubs to satisfy strict mypy
    # without breaking runtime instantiation.
    RedisClient = Redis[Any]
else:
    RedisClient = Redis

settings = get_settings()
redis_client: RedisClient = Redis.from_url(str(settings.redis_url), decode_responses=True)


async def ping_redis() -> bool:
    """Check whether Redis responds to PING.

    Returns:
        True when Redis responds successfully, otherwise False.
    """

    try:
        response = await redis_client.ping()
    except (OSError, RedisError):
        return False
    return bool(response)


async def close_redis() -> None:
    """Close the shared Redis client.

    redis>=5 exposes both ``aclose`` (preferred) and ``close``; we use
    ``close`` here because types-redis 4.6 stubs predate ``aclose``.
    """

    await redis_client.close()
