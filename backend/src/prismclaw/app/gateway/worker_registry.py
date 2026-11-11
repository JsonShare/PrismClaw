from __future__ import annotations

import logging
import os
import secrets
import socket
import time

logger = logging.getLogger(__name__)

_REGISTER_PRUNE_FLOOR_SECONDS = 30 * 86400
_LAZY_PRUNE_MULTIPLIER = 10


def generate_worker_id() -> str:
    """Generate a unique worker identifier for this process.

    Format: ``worker:{hostname}:{pid}:{4hex}``

    - hostname: human-readable host (debugging, ops)
    - pid: distinguishes multiple workers on the same host
    - 4hex random suffix: distinguishes a restarted process on the same
      host with the same PID (rare but possible after a quick restart)

    See design D2.
    """
    return f"worker:{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(2)}"


class WorkerRegistry:
    def __init__(
        self,
        redis_client=None,
        worker_id: str = "",
        key: str = "prismclaw:workers",
        heartbeat_interval: int = 30,
    ):
        self._redis = redis_client
        self._worker_id = worker_id
        self._key = key
        self._heartbeat_interval = heartbeat_interval

    @property
    def available(self) -> bool:
        return self._redis is not None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def register(self) -> None:
        if not self.available:
            return
        now = time.time()
        floor_cutoff = now - _REGISTER_PRUNE_FLOOR_SECONDS
        pipe = self._redis.pipeline()
        pipe.zadd(self._key, {self._worker_id: now})
        pipe.zremrangebyscore(self._key, "-inf", floor_cutoff)
        await pipe.execute()

    async def heartbeat(self) -> None:
        await self.register()

    async def deregister(self) -> None:
        if not self.available:
            return
        await self._redis.zrem(self._key, self._worker_id)

    async def active_workers(self, stale_threshold: float = 90.0) -> list[dict]:
        if not self.available:
            return [{"id": self._worker_id, "last_heartbeat": time.time(), "status": "healthy"}]

        now = time.time()
        lazy_cutoff = now - stale_threshold * _LAZY_PRUNE_MULTIPLIER
        try:
            await self._redis.zremrangebyscore(self._key, "-inf", lazy_cutoff)
        except Exception:
            logger.debug(
                "worker_registry.lazy_prune failed (non-fatal)", exc_info=True
            )

        members = await self._redis.zrangebyscore(self._key, "-inf", "+inf", withscores=True)
        result = []
        for member, score in members or []:
            worker_id = member.decode() if isinstance(member, bytes) else member
            age = now - score
            if age < stale_threshold:
                status = "healthy"
            elif age < stale_threshold * 1.5:
                status = "stale"
            else:
                status = "dead"
            result.append(
                {
                    "id": worker_id,
                    "last_heartbeat": score,
                    "status": status,
                    "age_seconds": round(age, 1),
                }
            )
        return result
