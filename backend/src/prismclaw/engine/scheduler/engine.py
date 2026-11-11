"""Scheduler engine — 1s tick loop, leader-only, with nextFireAt caching.

Design aligned with Kairos (Claude-Code cronScheduler.ts):
- Deterministic jitter per task ID (anti-thundering-herd)
- In-memory nextFireAt cache (invalidated on CRUD)
- Leader election via LockManager (not WorkerRegistry list ordering)
- Missed task detection on first leader tick (leader-only, once)

Cache invalidation across workers (cloud mode):
- The nextFireAt cache is process-local. A CRUD performed on worker A must
  reach the leader (possibly worker B). Two cooperating mechanisms:
  1. Redis pub/sub on INVALIDATE_CHANNEL — the leader subscribes and pops the
     affected entry (or clears everything on an empty payload).
  2. A ``_cache_dirty`` flag — set when the subscription is unavailable, so the
     next tick rebuilds the whole cache from the store. Local (same-process)
     invalidation goes through ``invalidate_cache`` directly and also sets the
     flag as a backstop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from croniter import croniter

from prismclaw.schema.scheduler import (
    INVALIDATE_CHANNEL,
    ScheduledTask,
    ScheduledTaskRun,
    make_run_id,
)
from prismclaw.schema.scheduler import (
    SchedulerStoreProtocol as SchedulerStore,
)

logger = logging.getLogger(__name__)


class SchedulerEngine:
    """Drives scheduled task firing. Spawned on TaskManager as a long-running coroutine."""

    def __init__(
        self,
        *,
        store: SchedulerStore,
        dispatch_enqueue,  # async callable(SchedulerDispatchJob) -> None
        settings,  # SchedulerSettings
        lock_manager=None,  # LockManager | None
        redis=None,  # aioredis.Redis | None — enables cross-worker invalidation
        worker_id: str = "",  # this process's identity (embedded in the lock)
    ) -> None:
        self._store = store
        self._dispatch_enqueue = dispatch_enqueue
        self._settings = settings
        self._lock = lock_manager
        self._redis = redis
        self._worker_id = worker_id
        self._lock_token: str | None = None
        self._failed_holder_reported = False
        self._last_failed_holder: str | None = None
        self._next_fire_cache: dict[str, float] = {}
        self._cache_dirty = False
        self._recovered = False
        self._running = False
        self._stopping = False
        self._pubsub = None
        self._watch_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._redis_heartbeat_task: asyncio.Task | None = None

    # ── Public API ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Main loop. Call via TaskManager.spawn."""
        self._running = True
        self._stopping = False
        logger.info(
            "scheduler.engine.start",
            extra={"tick_s": self._settings.tick_interval_seconds},
        )
        if self._redis is not None:
            self._watch_task = asyncio.create_task(
                self._watch_invalidation_with_reconnect(),
                name="scheduler-invalidation-watch",
            )
            # Redis PING heartbeat: keeps Docker NAT connections alive for
            # idle Redis connections (including pubsub) to prevent timeout
            # closure. Without this, Docker may close idle connections after
            # ~300s of inactivity.
            self._redis_heartbeat_task = asyncio.create_task(
                self._redis_ping_loop(),
                name="scheduler-redis-heartbeat",
            )
        # Leader-lock heartbeat: renews the lock on a fixed 2s cadence,
        # decoupled from tick duration. Without this, a slow tick (many await
        # store round-trips) could exceed the 5s lock TTL and let another worker
        # co-dispatch. The heartbeat keeps the lock alive regardless of tick load.
        if self._lock is not None:
            self._heartbeat_task = asyncio.create_task(
                self._lock_heartbeat_loop(),
                name="scheduler-lock-heartbeat",
            )
            logger.info("scheduler.lock.heartbeat_started")
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("scheduler.engine.tick_error")
            await asyncio.sleep(self._settings.tick_interval_seconds)

    def stop(self) -> None:
        self._running = False
        self._stopping = True
        if self._watch_task is not None and not self._watch_task.done():
            self._watch_task.cancel()
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._redis_heartbeat_task is not None and not self._redis_heartbeat_task.done():
            self._redis_heartbeat_task.cancel()
        if self._pubsub is not None:
            # Best-effort close; the watch loop's finally also closes on exit.
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon(lambda: asyncio.ensure_future(self._close_pubsub()))
            except RuntimeError:
                pass  # No running loop; connection is dropped with the process
        if self._lock_token and self._lock:
            # Best-effort release; lock will expire via TTL if this fails
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon(lambda: asyncio.ensure_future(self._release_lock()))
            except RuntimeError:
                pass  # No running loop; lock expires via TTL

    async def _close_pubsub(self) -> None:
        pubsub, self._pubsub = self._pubsub, None
        if pubsub is None:
            return
        try:
            await pubsub.unsubscribe(INVALIDATE_CHANNEL)
            await pubsub.close()
        except Exception:  # noqa: BLE001 — teardown is best-effort
            logger.debug("scheduler.pubsub.close_error", exc_info=True)

    def invalidate_cache(self, task_id: str | None = None) -> None:
        """Called by the API/tool layer after CRUD operations (same process).

        Cross-worker invalidation arrives via the pub/sub watcher, which calls
        this same method. The dirty flag is a backstop: the next leader tick
        rebuilds the cache from the store regardless of which entry changed.
        """
        if task_id:
            self._next_fire_cache.pop(task_id, None)
        else:
            self._next_fire_cache.clear()
        self._cache_dirty = True

    # ── Tick ───────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        if not await self._ensure_leader():
            return

        # B.2 — reconcile the process-local cache after any invalidation. The
        # specific entry was already popped on the invalidation event; clearing
        # here is the backstop that guarantees consistency with the store.
        if self._cache_dirty:
            self._next_fire_cache.clear()
            self._cache_dirty = False

        # B.4 — missed-task recovery runs once, on the first tick this worker
        # leads. Gating on the lock prevents every worker from recovering (and
        # double-dispatching) at startup; the flag makes it run exactly once.
        if not self._recovered:
            self._recovered = True
            await self._recover_missed()

        now = time.time()
        tasks = await self._store.list_tasks(enabled_only=True)

        dispatch_coros = []
        for task in tasks:
            if self._is_expired(task, now):
                task.enabled = False
                dispatch_coros.append(self._expire_task(task))
                continue

            next_at = self._next_fire_cache.get(task.id)
            if next_at is None:
                next_at = self._compute_next_fire(task)
                if next_at is None:
                    continue
                self._next_fire_cache[task.id] = next_at

            if now >= next_at:
                fire_slot = self._cron_fire_timestamp(task)
                if fire_slot is not None:
                    dispatch_coros.append(self._dispatch(task, now, fire_slot))
                # Reschedule from now (not from next_at) to avoid rapid catch-up
                new_next = self._compute_next_fire(task, anchor=now)
                if new_next is not None:
                    self._next_fire_cache[task.id] = new_next
                else:
                    self._next_fire_cache.pop(task.id, None)

        # Fire all due dispatches concurrently (DB writes + queue enqueues)
        if dispatch_coros:
            await asyncio.gather(*dispatch_coros, return_exceptions=True)

        # Evict cache entries for tasks no longer present
        live_ids = {t.id for t in tasks}
        stale = [tid for tid in self._next_fire_cache if tid not in live_ids]
        for tid in stale:
            del self._next_fire_cache[tid]

    # ── Dispatch ───────────────────────────────────────────────────────

    async def _expire_task(self, task: ScheduledTask) -> None:
        """Disable and persist an expired task."""
        await self._store.update_status(task.id, "disabled")
        await self._store.put_task(task)
        self._next_fire_cache.pop(task.id, None)
        logger.info("scheduler.task.expired", extra={"task_id": task.id})

    async def _dispatch(self, task: ScheduledTask, now: float, fire_slot: float) -> None:
        from prismclaw.schema.scheduler import SchedulerDispatchJob

        run = ScheduledTaskRun(
            id=make_run_id(task.id, fire_slot),
            task_id=task.id,
            fired_at=now,
            status="pending",
        )
        # Atomic: insert the run AND stamp the task's last_fired_at in one
        # transaction. A crash between the two would leave last_fired_at stale,
        # causing the next tick to re-dispatch the same fire window.
        won = await self._store.add_run(run, fired_task_id=task.id, fired_at=now)
        if not won:
            logger.info(
                "scheduler.task.fire_deduped",
                extra={"task_id": task.id, "fire_slot": fire_slot},
            )
            return

        job = SchedulerDispatchJob(
            run_id=run.id,
            task_id=task.id,
            task_name=task.name,
            instruction=task.instruction,
            user_id=task.user_id,
            workspace_id=task.workspace_id,
            fired_at=now,
        )
        try:
            await self._dispatch_enqueue(job)
        except Exception:
            await self._store.update_run(
                run.id, status="failed", error="dispatch_enqueue_failed"
            )
            logger.exception(
                "scheduler.task.dispatch_failed",
                extra={"task_id": task.id, "run_id": run.id},
            )
            return
        logger.info(
            "scheduler.task.fired",
            extra={"task_id": task.id, "run_id": run.id, "cron": task.cron_expr},
        )

    # ── Cron + Jitter ──────────────────────────────────────────────────

    def _cron_fire_timestamp(
        self, task: ScheduledTask, anchor: float | None = None
    ) -> float | None:
        base_time = anchor or task.last_fired_at or task.created_at
        try:
            cron = croniter(task.cron_expr, datetime.fromtimestamp(base_time).astimezone())
            next_dt = cron.get_next(datetime)
        except (ValueError, KeyError):
            logger.warning(
                "scheduler.cron.parse_error",
                extra={"task_id": task.id, "cron": task.cron_expr},
            )
            return None
        return next_dt.timestamp()

    def _compute_next_fire(self, task: ScheduledTask, anchor: float | None = None) -> float | None:
        # Cron expressions entered through the frontend are in the user's local
        # timezone (frontend shows "每天 10:00" — Beijing / Asia/Shanghai, NOT UTC).
        # Using timezone.utc here would fire 8h off (10:00 UTC = 18:00 Beijing).
        # Use astimezone() to anchor croniter in the server's local TZ — same
        # convention as DreamSettings.nightly_timezone which defaults to "local".
        base_time = anchor or task.last_fired_at or task.created_at
        next_ts = self._cron_fire_timestamp(task, anchor)
        if next_ts is None:
            return None

        # Deterministic jitter: stable per-task hash → [0, 1)
        jitter_frac = self._jitter_fraction(task.id)
        interval = next_ts - base_time
        jitter = jitter_frac * self._settings.jitter_fraction * interval
        jitter = min(jitter, self._settings.jitter_cap_seconds)

        return next_ts + jitter

    @staticmethod
    def _jitter_fraction(task_id: str) -> float:
        """Deterministic hash → [0, 1). Same algorithm as Kairos."""
        hex_prefix = task_id[:8].ljust(8, "0")
        try:
            return int(hex_prefix, 16) / 0x100000000
        except ValueError:
            return 0.5

    def _is_expired(self, task: ScheduledTask, now: float) -> bool:
        if task.max_age_days == 0:
            return False
        return now - task.created_at > task.max_age_days * 86400

    # ── Missed task recovery ───────────────────────────────────────────

    async def _recover_missed(self) -> None:
        """On startup, detect tasks that should have fired while we were down."""
        now = time.time()
        tasks = await self._store.list_tasks(enabled_only=True)
        recovered = 0

        for task in tasks:
            if task.missed_policy != "run_once":
                continue
            if task.last_fired_at is None and task.created_at is None:
                continue

            anchor = task.last_fired_at or task.created_at
            try:
                cron = croniter(task.cron_expr, datetime.fromtimestamp(anchor).astimezone())
                next_dt = cron.get_next(datetime)
            except (ValueError, KeyError):
                continue

            if next_dt.timestamp() < now:
                # Missed at least one fire — dispatch once
                await self._dispatch(task, now, next_dt.timestamp())
                recovered += 1
                # Refresh the cache anchored at 'now' so the regular tick loop
                # in this same tick does not recompute from the stale in-memory
                # last_fired_at and dispatch the task a second time.
                new_next = self._compute_next_fire(task, anchor=now)
                if new_next is not None:
                    self._next_fire_cache[task.id] = new_next
                logger.info("scheduler.missed.recovered", extra={"task_id": task.id})

        if recovered:
            logger.info("scheduler.missed.total", extra={"count": recovered})

    # ── Cross-worker cache invalidation ────────────────────────────────

    async def _watch_invalidation_with_reconnect(self) -> None:
        """Subscribe to INVALIDATE_CHANNEL and keep the subscription alive.

        Runs for the engine's lifetime (spawned by ``start`` when redis is
        configured). On any connection loss the cache is marked dirty — the
        leader's next tick then rebuilds it from the store, so invalidation
        messages missed while disconnected still take effect. Reconnect uses
        exponential backoff, mirroring gateway.forwarder.ForwardConsumer.
        """
        backoff_s = 1.0
        max_backoff_s = 30.0
        while not self._stopping:
            try:
                self._pubsub = self._redis.pubsub()
                await self._pubsub.subscribe(INVALIDATE_CHANNEL)
                logger.info(
                    "scheduler.invalidation.subscribed",
                    extra={"channel": INVALIDATE_CHANNEL},
                )
                backoff_s = 1.0
                async for message in self._pubsub.listen():
                    if self._stopping:
                        break
                    if message.get("type") != "message":
                        continue
                    self._watch_invalidation(message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — keep watching across redis blips
                if self._stopping:
                    return
                # Subscription is unavailable: reconcile from the store on the
                # next tick rather than trusting a possibly-stale cache.
                self._cache_dirty = True
                logger.warning(
                    "scheduler.invalidation.subscription_lost retry_in=%.1fs",
                    backoff_s,
                    exc_info=True,
                )
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, max_backoff_s)
            finally:
                await self._close_pubsub()

    def _watch_invalidation(self, data: object) -> None:
        """Handle one pub/sub message: payload is the affected task_id."""
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        task_id = data if isinstance(data, str) else ""
        # Route through invalidate_cache so remote invalidations share the same
        # pop/clear + dirty-flag backstop as local ones.
        self.invalidate_cache(task_id or None)
        logger.debug(
            "scheduler.invalidation.received",
            extra={"task_id": task_id or "<all>"},
        )

    # ── Leader election ────────────────────────────────────────────────

    async def _ensure_leader(self) -> bool:
        """Acquire the leader lock if not already held.

        Renewal is handled by :meth:`_lock_heartbeat_loop`, NOT here — so a
        slow tick cannot let the lock expire mid-iteration. Returns True if
        this worker currently holds leadership.
        """
        if self._lock is None:
            return True  # Single-worker mode, no lock needed

        if self._lock_token is not None:
            return True  # Still holding (heartbeat keeps it alive)

        from prismclaw.schema.shared import LockAcquireError

        try:
            new_token = await self._lock.acquire(
                self._settings.leader_lock_key,
                ttl_ms=self._settings.leader_lock_ttl_ms,
                owner=self._worker_id,
            )
            self._lock_token = new_token
            self._failed_holder_reported = False
            logger.info(
                "scheduler.lock.acquired",
                extra={
                    "worker": self._worker_id,
                    "token_suffix": new_token[-8:],
                },
            )
            return True
        except LockAcquireError:
            await self._log_acquire_failed()
            return False  # Another worker holds the leader lock

    async def _log_acquire_failed(self) -> None:
        """Log a failed leader-lock attempt as routine INFO, naming the holder.

        In multi-worker mode N-1 followers fail every tick by design — that is
        not a warning condition. The record carries the current holder (read
        from the lock value) so "who is the leader" is answerable from logs.
        Emission is change-driven: once per holder, again when the holder
        changes (leader flip), re-armed after this worker itself holds the
        lock (so a subsequent loss re-reports even for the same holder).
        """
        holder: str | None
        try:
            holder = await self._lock.owner_of(self._settings.leader_lock_key)
        except Exception:
            logger.debug("scheduler.lock.holder_lookup_failed", exc_info=True)
            holder = "unknown"
        if not self._failed_holder_reported or holder != self._last_failed_holder:
            logger.info(
                "scheduler.lock.acquire_failed",
                extra={
                    "key": self._settings.leader_lock_key,
                    "holder": holder,
                    "worker": self._worker_id,
                },
            )
            self._failed_holder_reported = True
            self._last_failed_holder = holder

    async def _lock_heartbeat_loop(self) -> None:
        """Renew the leader lock on a TTL-proportional cadence, decoupled from ticks.

        Cadence = min(2s, TTL/3)。固定 2s 在 TTL 可被调小（或事件循环被长任务
        阻塞）时不再成立：只要两次续约间隔有任意一次超过 TTL，锁就自然过期，
        领导权丢失并非「renew 返回失败」而是「根本没来得及 renew」。按 TTL 的
        三分之一节拍续约，保证至少容忍两次连续延误。

        If renewal definitively fails (``renew`` returns False — the lock was
        expired or stolen), leadership is relinquished (``_lock_token = None``)
        so the next tick stops dispatching and re-attempts acquisition. On a
        transient redis error we keep the token and retry **quickly** (0.5s)
        instead of waiting a full interval — the TTL covers a brief blip, but
        sleeping a full interval burns a third of that budget per failure.
        """
        ttl_ms = int(self._settings.leader_lock_ttl_ms)
        interval_s = min(2.0, max(0.1, ttl_ms / 3000.0))
        logger.info(
            "scheduler.lock.heartbeat_loop_entered",
            extra={"interval_s": round(interval_s, 3), "ttl_ms": ttl_ms},
        )
        renew_count = 0
        failures = 0
        delay = interval_s
        try:
            while not self._stopping:
                await asyncio.sleep(delay)
                delay = interval_s
                if self._lock_token is None or self._lock is None:
                    continue
                try:
                    renewed = await self._lock.renew(
                        self._settings.leader_lock_key,
                        self._lock_token,
                        ttl_ms=ttl_ms,
                    )
                    renew_count += 1
                    failures = 0
                    if not renewed:
                        logger.warning(
                            "scheduler.lock.heartbeat_lost",
                            extra={
                                "key": self._settings.leader_lock_key,
                                "worker": self._worker_id,
                                "token_suffix": self._lock_token[-8:],
                                "renew_count": renew_count,
                            },
                        )
                        self._lock_token = None
                    elif renew_count % 5 == 0:
                        logger.info(
                            "scheduler.lock.heartbeat_ok",
                            extra={"renew_count": renew_count},
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failures += 1
                    # 连续失败 3 次（≈ 一个 TTL 量级）说明不是噪声，升到 warning；
                    # 单次抖动保持 debug，避免刷日志本身又成为负载。
                    log_fn = logger.warning if failures >= 3 else logger.debug
                    log_fn(
                        "scheduler.lock.heartbeat_error",
                        extra={
                            "key": self._settings.leader_lock_key,
                            "failures": failures,
                        },
                        exc_info=True,
                    )
                    delay = min(0.5, interval_s)
        except asyncio.CancelledError:
            return

    async def _redis_ping_loop(self) -> None:
        """Send periodic PING to Redis to keep all connections alive.

        Pings both the main connection pool AND the PubSub channel, so that
        idle connections (especially the PubSub listen channel waiting for
        messages) are never silently closed by Docker NAT or OS TCP keepalive
        timeouts. Without this, the PubSub ``listen()`` would eventually be
        interrupted by a socket timeout, even with ``socket_timeout=None``.
        """
        try:
            while not self._stopping:
                await asyncio.sleep(30)
                if self._redis is None or self._stopping:
                    continue
                try:
                    await self._redis.ping()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug("scheduler.redis.ping_error", exc_info=True)
                # Also PING the PubSub connection to keep its TCP socket alive
                pubsub = self._pubsub
                if pubsub is not None:
                    try:
                        await pubsub.ping()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.debug("scheduler.redis.pubsub_ping_error", exc_info=True)
        except asyncio.CancelledError:
            return

    async def _release_lock(self) -> None:
        if self._lock and self._lock_token:
            await self._lock.release(self._settings.leader_lock_key, self._lock_token)
            self._lock_token = None
