from __future__ import annotations

import logging

import redis.asyncio as aioredis
from redis.retry import Retry
from redis.backoff import ExponentialBackoff

from prismclaw.infra.settings import RedisSettings

logger = logging.getLogger(__name__)

_RETRYABLE_ERRORS = (
    ConnectionError,
    ConnectionResetError,
    TimeoutError,
    OSError,
)

_SHARED_RETRY = Retry(
    ExponentialBackoff(base=1.0, cap=30.0),
    retries=5,
)


async def get_client(settings: RedisSettings) -> aioredis.Redis:
    url = settings.build_url()
    client: aioredis.Redis = aioredis.from_url(
        url,
        decode_responses=True,
        encoding="utf-8",
        protocol=2,
        socket_keepalive=True,
        socket_timeout=None,
        socket_connect_timeout=10,
        health_check_interval=30,
        retry=_SHARED_RETRY,
        retry_on_error=_RETRYABLE_ERRORS,
    )
    return client


async def close_client(client: aioredis.Redis | None) -> None:
    if client is None:
        return
    try:
        await client.aclose()
    except Exception:
        logger.exception("error closing Redis client")


async def ping(client: aioredis.Redis) -> bool:
    try:
        return await client.ping()
    except Exception:
        logger.debug("Redis ping failed", exc_info=True)
        return False
