from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

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
    except asyncio.CancelledError:
        # 停机期取消是预期行为：连接池已停止服务，不能被回收前再抛出
        # CancelledError（否则 uvicorn 会把整个 lifespan 退出记成 ERROR）。
        logger.warning("Redis client close interrupted by shutdown cancellation")
    except Exception:
        logger.exception("error closing Redis client")


async def ping(client: aioredis.Redis) -> bool:
    try:
        return await client.ping()
    except Exception:
        logger.debug("Redis ping failed", exc_info=True)
        return False
