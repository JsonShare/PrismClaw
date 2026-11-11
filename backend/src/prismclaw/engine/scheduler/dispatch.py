"""Scheduler dispatch — Redis Streams used as event notification.

A stream message is a one-shot wake-up signal, not a work credential::

    XREADGROUP → XACK immediately → spawn(handler(job))

The consumer group provides single delivery across workers. Nothing survives
a crashed worker (at-most-once): a scheduled run is an ordinary chat turn
with no execution time limit, so any PEL/reclaim/retry scheme would have to
guess a time window and would re-introduce a lease. Trigger idempotency
therefore lives in ``store.add_run`` (deterministic run id + ``ON CONFLICT
DO NOTHING``), not in the queue.

Concurrency is bounded locally by :class:`SchedulerDispatchConsumer`'s
semaphores (per-node back-pressure), which is unrelated to the queue model.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from prismclaw.schema.scheduler import SchedulerDispatchJob

logger = logging.getLogger(__name__)


class RedisSchedulerDispatchQueue:
    """Redis Streams notification queue for scheduled task firings."""

    def __init__(
        self,
        redis_client: Any,
        *,
        key_prefix: str = "prismclaw:scheduler:dispatch:",
        group_name: str = "scheduler-dispatch",
    ) -> None:
        self._redis = redis_client
        self._stream_key = f"{key_prefix}stream"
        self._group_name = group_name
        self._group_ready = False

    async def ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._redis.xgroup_create(
                self._stream_key, self._group_name, id="0-0", mkstream=True
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def enqueue(self, job: SchedulerDispatchJob) -> None:
        """Publish one notification for an already-claimed fire.

        The fire has been won via ``add_run`` before this point, so duplicate
        enqueue is structurally impossible and no dedupe marker is needed.
        """
        await self.ensure_group()
        await self._redis.xadd(
            self._stream_key,
            {
                "run_id": job.run_id,
                "task_id": job.task_id,
                "task_name": job.task_name,
                "instruction": job.instruction,
                "user_id": job.user_id,
                "workspace_id": job.workspace_id,
                "fired_at": str(job.fired_at),
            },
        )

    async def consume(
        self,
        handler: Callable[[SchedulerDispatchJob], Awaitable[None]],
        *,
        block_ms: int = 5000,
        count: int = 5,
    ) -> None:
        """Read notifications, ACK before spawning work, run forever.

        XACK happens before the handler starts: the message is a wake-up
        signal, not an in-flight marker. If this process dies the notification
        is already gone and the run keeps whatever status the executor reached
        (``pending`` or ``running``) — an honest state, with no automatic
        recovery. ``inflight`` exists so shutdown can cancel spawned tasks
        and so their unexpected failures are logged (never retried).
        """
        await self.ensure_group()
        consumer_name = f"worker-{id(self)}"
        inflight: set[asyncio.Task] = set()

        def _on_done(t: asyncio.Task) -> None:
            """Drop the task and surface unexpected handler failures.

            The executor only catches exceptions from its run_chat block;
            failures in the surrounding state machine (store writes,
            notifications) still escape. With no reclaim/retry, a log is
            the whole response.
            """
            inflight.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "scheduler.dispatch.handler_error",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        while True:
            try:
                results = await self._redis.xreadgroup(
                    self._group_name,
                    consumer_name,
                    {self._stream_key: ">"},
                    count=count,
                    block=block_ms,
                )
            except asyncio.CancelledError:
                for t in inflight:
                    t.cancel()
                raise
            except Exception:
                logger.exception("scheduler.dispatch.read_error")
                await asyncio.sleep(2)
                continue

            if not results:
                continue

            for _stream, messages in results:
                for msg_id, fields in messages:
                    try:
                        await self._redis.xack(
                            self._stream_key, self._group_name, msg_id
                        )
                    except Exception:
                        # A failed ACK must not kill the consumer loop. There
                        # is no reclaim, so dropping this one-shot notification
                        # is consistent with at-most-once.
                        logger.exception(
                            "scheduler.dispatch.ack_error",
                            extra={"msg_id": str(msg_id)},
                        )
                        continue
                    job = self._parse_message(msg_id, fields)
                    if job is None:
                        continue
                    t = asyncio.create_task(handler(job))
                    inflight.add(t)
                    t.add_done_callback(_on_done)

    @staticmethod
    def _parse_message(msg_id: Any, fields: dict) -> SchedulerDispatchJob | None:
        try:
            def _s(key: str) -> str:
                # A missing field must make parsing fail (KeyError -> None),
                # not silently produce an empty-valued job that gets executed.
                v = fields[key]
                return v.decode() if isinstance(v, bytes) else str(v)

            return SchedulerDispatchJob(
                run_id=_s("run_id"),
                task_id=_s("task_id"),
                task_name=_s("task_name"),
                instruction=_s("instruction"),
                user_id=_s("user_id"),
                workspace_id=_s("workspace_id"),
                fired_at=float(_s("fired_at") or "0"),
            )
        except Exception:
            logger.warning(
                "scheduler.dispatch.parse_error", extra={"msg_id": str(msg_id)}
            )
            return None


class SchedulerDispatchConsumer:
    """Wraps the queue consume loop with concurrency control."""

    def __init__(
        self,
        *,
        queue: RedisSchedulerDispatchQueue,
        handler: Callable[[SchedulerDispatchJob], Awaitable[None]],
        max_concurrent: int = 3,
        max_per_user: int = 2,
    ) -> None:
        self._queue = queue
        self._handler = handler
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_per_user = max_per_user
        self._user_semaphores: dict[str, asyncio.Semaphore] = {}

    async def start(self) -> None:
        """Main consume loop. Spawn on TaskManager."""
        logger.info("scheduler.consumer.start")
        await self._queue.consume(self._guarded_handle)

    async def _guarded_handle(self, job: SchedulerDispatchJob) -> None:
        user_sem = self._user_semaphores.setdefault(
            job.user_id, asyncio.Semaphore(self._max_per_user)
        )
        async with self._semaphore:
            async with user_sem:
                await self._handler(job)
