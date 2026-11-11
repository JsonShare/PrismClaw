from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .dispatch import (
    DreamDispatchJob,
    build_light_dispatch_job,
    build_nightly_dispatch_job,
)
from .lock import DreamLockProvider
from prismclaw.schema.dream.dream import DreamStateStore
from prismclaw.schema.dream.dream_types import DreamSessionState

logger = logging.getLogger(__name__)


class DreamEngineLike(Protocol):
    async def run_light(self, session_key: str) -> dict[str, Any]: ...
    async def run_nightly(self, session_key: str, *, include_light: bool = False) -> dict[str, Any]: ...


class DreamSchedulerSettings(Protocol):
    enabled: bool
    idle_check_interval_seconds: int
    idle_threshold_seconds: int
    nightly_check_interval_seconds: int
    nightly_timezone: str
    nightly_start_hour_local: int
    nightly_end_hour_local: int
    nightly_catch_up_grace_minutes: int
    nightly_max_sessions: int
    multi_worker_scan_policy: str


@dataclass(slots=True)
class _NightlyWindow:
    start_at: datetime
    end_at: datetime
    catch_up: bool = False


class DreamScheduler:
    def __init__(
        self,
        *,
        dreaming_engine: DreamEngineLike | None,
        settings: DreamSchedulerSettings,
        time_fn: Any = None,
        state_store: DreamStateStore | None = None,
        lock_provider: DreamLockProvider | None = None,
        task_manager: Any | None = None,
        worker_registry: Any | None = None,
        worker_stale_threshold_s: float = 90.0,
        dispatch_queue: Any | None = None,
    ) -> None:
        self._dreaming_engine = dreaming_engine
        self._settings = settings
        self._time_fn = time_fn or time.time
        self._nightly_timezone = _resolve_nightly_timezone(
            getattr(settings, "nightly_timezone", "local")
        )
        if state_store is None:
            raise ValueError("DreamScheduler requires an explicit state_store")
        if lock_provider is None:
            raise ValueError("DreamScheduler requires an explicit lock_provider")
        self._state_store = state_store
        self._lock_provider = lock_provider
        self._task_manager = task_manager
        self._worker_registry = worker_registry
        self._worker_stale_threshold_s = max(1.0, float(worker_stale_threshold_s))
        self._multi_worker_scan_policy = str(
            getattr(settings, "multi_worker_scan_policy", "leader_only") or "leader_only"
        ).strip().lower()
        self._dispatch_queue = dispatch_queue
        self._started = False
        if self._task_manager is not None:
            self._task_manager.spawn(
                "dream-scheduler:sanitize",
                self._sanitize_restored_states(),
                category="dream",
            )

    @property
    def state_store(self) -> DreamStateStore:
        return self._state_store

    def start(self, task_manager: Any | None) -> None:
        if task_manager is not None:
            self._task_manager = task_manager
        if self._started or task_manager is None or not self._settings.enabled:
            return
        self._started = True
        task_manager.spawn(
            "dream-scheduler:idle",
            self._idle_loop(),
            category="dream",
        )
        task_manager.spawn(
            "dream-scheduler:nightly",
            self._nightly_loop(),
            category="dream",
        )

    async def mark_activity(self, session_key: str, *, dirty_delta: int = 1) -> DreamSessionState:
        now = float(self._time_fn())
        state = await self._state_store.get(session_key)
        if state is None:
            state = DreamSessionState(session_key=session_key)
        state.last_activity_at = now
        state.memory_dirty_score = max(0, state.memory_dirty_score + max(0, dirty_delta))
        state.light_pending = True
        state.nightly_pending = True
        state.last_enqueue_activity_at = now
        state.last_enqueue_activity_status = "accepted"
        state.last_enqueue_activity_detail = None
        self._sync_pending_flags(state)
        await self._state_store.put(state)
        return state

    async def enqueue_nightly(self, session_key: str) -> DreamSessionState:
        state = await self._state_store.get(session_key)
        if state is None:
            state = DreamSessionState(session_key=session_key, last_activity_at=float(self._time_fn()))
        now = float(self._time_fn())
        state.nightly_pending = True
        state.last_enqueue_nightly_at = now
        state.last_enqueue_nightly_status = "accepted"
        state.last_enqueue_nightly_detail = None
        self._sync_pending_flags(state)
        await self._state_store.put(state)
        return state

    async def get_state(self, session_key: str) -> DreamSessionState | None:
        return await self._state_store.get(session_key)

    async def run_idle_scan_once(self) -> int:
        if (
            self._dreaming_engine is None
            or not self._settings.enabled
            or self._task_manager is None
        ):
            return 0
        if not await self._should_scan_on_this_worker():
            return 0
        now = float(self._time_fn())
        processed = 0
        for state in await self._state_store.list_states():
            session_key = state.session_key
            if not state.light_pending:
                continue
            if now - state.last_activity_at < self._settings.idle_threshold_seconds:
                continue
            if await self._enqueue_light(session_key):
                processed += 1
        return processed

    async def run_nightly_scan_once(self, *, now: datetime | None = None) -> int:
        if (
            self._dreaming_engine is None
            or not self._settings.enabled
            or self._task_manager is None
        ):
            return 0
        if not await self._should_scan_on_this_worker():
            return 0
        current = self._normalize_nightly_now(now)
        nightly_window = self._resolve_nightly_window(current)
        if nightly_window is None:
            return 0
        processed = 0
        candidates = sorted(
            (
                state
                for state in await self._state_store.list_states()
                if state.nightly_pending and self._should_run_nightly_window(state, nightly_window)
            ),
            key=lambda item: (-item.memory_dirty_score, item.last_activity_at),
        )
        for state in candidates[: max(1, self._settings.nightly_max_sessions)]:
            if await self._enqueue_nightly(state.session_key, nightly_window=nightly_window):
                processed += 1
        return processed

    async def _enqueue_light(self, session_key: str) -> bool:
        if self._has_active_worker(session_key):
            return False
        dispatched = await self._enqueue_dispatched_job(build_light_dispatch_job(session_key))
        if dispatched:
            return True
        if self._task_manager is None or self._has_active_worker(session_key):
            return False
        try:
            self._task_manager.spawn(
                f"dream-worker:light:{session_key}",
                self._run_light_worker(session_key),
                category="dream",
                owner=session_key,
            )
        except Exception:
            logger.warning("failed to enqueue light dream for %s", session_key, exc_info=True)
            return False
        return True

    async def _enqueue_nightly(
        self,
        session_key: str,
        *,
        nightly_window: _NightlyWindow,
    ) -> bool:
        if self._has_active_worker(session_key):
            return False
        dispatched = await self._enqueue_dispatched_job(
            build_nightly_dispatch_job(
                session_key,
                nightly_window_start_at=nightly_window.start_at.timestamp(),
                nightly_window_end_at=nightly_window.end_at.timestamp(),
            )
        )
        if dispatched:
            return True
        if self._task_manager is None or self._has_active_worker(session_key):
            return False
        try:
            self._task_manager.spawn(
                f"dream-worker:nightly:{session_key}",
                self._run_nightly_worker(session_key, nightly_window=nightly_window),
                category="dream",
                owner=session_key,
            )
        except Exception:
            logger.warning("failed to enqueue nightly dream for %s", session_key, exc_info=True)
            return False
        return True

    async def _enqueue_dispatched_job(self, job: DreamDispatchJob) -> bool:
        if self._dispatch_queue is None:
            return False
        try:
            return bool(await self._dispatch_queue.enqueue(job))
        except Exception:
            logger.warning("dream dispatch enqueue failed for %s", job.session_key, exc_info=True)
            return False

    async def run_dispatched_job(self, job: DreamDispatchJob) -> bool:
        if job.job_kind == "light":
            return await self._run_light_worker(job.session_key)
        if job.job_kind == "nightly":
            nightly_window = self._nightly_window_from_job(job)
            if nightly_window is None:
                return False
            return await self._run_nightly_worker(job.session_key, nightly_window=nightly_window)
        return False

    async def _run_light_worker(self, session_key: str) -> bool:
        assert self._dreaming_engine is not None
        batch_started_at = float(self._time_fn())
        lease = await self._lock_provider.try_acquire(session_key, owner_label="idle-light")
        if lease is None:
            # 锁被占说明该会话的 dream 正在被执行，本消息应 ack 丢弃而非重试进死信
            return True
        state = (await self._state_store.get(session_key)) or DreamSessionState(session_key=session_key)
        try:
            state.dream_lock_owner = "idle-light"
            state.dream_batch_id = f"light:{int(batch_started_at)}"
            await self._state_store.put(state)
            try:
                await self._dreaming_engine.run_light(session_key)
                lease.ensure_valid()
                latest_state = (await self._state_store.get(session_key)) or state
                latest_state.last_light_at = float(self._time_fn())
                if latest_state.last_activity_at <= batch_started_at:
                    latest_state.light_pending = False
                state = latest_state
            except Exception:
                logger.warning("idle light dream failed for %s", session_key, exc_info=True)
            finally:
                state.dream_lock_owner = None
                self._sync_pending_flags(state)
                await self._state_store.put(state)
        finally:
            await lease.release()
        return True

    async def _run_nightly_worker(
        self,
        session_key: str,
        *,
        nightly_window: _NightlyWindow,
    ) -> bool:
        assert self._dreaming_engine is not None
        batch_started_at = float(self._time_fn())
        lease = await self._lock_provider.try_acquire(session_key, owner_label="nightly-batch")
        if lease is None:
            # 锁被占说明该会话的 dream 正在被执行，本消息应 ack 丢弃而非重试进死信
            return True
        state = (await self._state_store.get(session_key)) or DreamSessionState(session_key=session_key)
        try:
            state.dream_lock_owner = "nightly-batch"
            state.dream_batch_id = f"nightly:{int(batch_started_at)}"
            state.last_compile_started_at = batch_started_at
            state.last_compile_status = "running"
            include_light = state.light_pending
            await self._state_store.put(state)
            try:
                result = await self._dreaming_engine.run_nightly(
                    session_key,
                    include_light=include_light,
                )
                lease.ensure_valid()
                now = float(self._time_fn())
                latest_state = (await self._state_store.get(session_key)) or state
                if include_light:
                    latest_state.last_light_at = now
                if result.get("deep_sleep") is not None:
                    latest_state.last_deep_at = now
                if result.get("rem_sleep") is not None:
                    latest_state.last_rem_at = now
                latest_state.compile_cursor = latest_state.dream_batch_id
                latest_state.last_compile_finished_at = now
                latest_state.last_compile_status = "ok"
                deep_result = result.get("deep_sleep") if isinstance(result, dict) else None
                if isinstance(deep_result, dict):
                    latest_state.last_compiled_candidate_count = int(
                        deep_result.get("compiled_candidates", 0) or 0
                    )
                    latest_state.last_published_memory_count = int(
                        deep_result.get("published_memory_count", 0) or 0
                    )
                    latest_state.last_gate_rejected_entries = int(
                        deep_result.get("gate_rejected_entries", 0) or 0
                    )
                if latest_state.last_published_memory_count > 0 or result.get("rem_sleep") is not None:
                    latest_state.publish_watermark_at = now
                latest_state.last_nightly_window_started_at = nightly_window.start_at.timestamp()
                if latest_state.last_activity_at <= batch_started_at:
                    latest_state.light_pending = False
                    latest_state.nightly_pending = False
                    latest_state.memory_dirty_score = 0
                state = latest_state
            except Exception:
                state.last_compile_finished_at = float(self._time_fn())
                state.last_compile_status = "failed"
                logger.warning("nightly dream failed for %s", session_key, exc_info=True)
            finally:
                state.dream_lock_owner = None
                self._sync_pending_flags(state)
                await self._state_store.put(state)
        finally:
            await lease.release()
        return True

    async def _idle_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(max(1, self._settings.idle_check_interval_seconds))
                await self.run_idle_scan_once()
        except asyncio.CancelledError:
            return

    async def _nightly_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(max(1, self._settings.nightly_check_interval_seconds))
                await self.run_nightly_scan_once()
        except asyncio.CancelledError:
            return

    def _should_run_nightly_window(
        self,
        state: DreamSessionState,
        nightly_window: _NightlyWindow,
    ) -> bool:
        last_run = state.last_nightly_window_started_at
        if last_run is None:
            return True
        return last_run < nightly_window.start_at.timestamp()

    def _normalize_nightly_now(self, now: datetime | Any | None) -> datetime:
        if now is not None and not isinstance(now, datetime):
            return datetime.now(self._nightly_timezone).replace(
                hour=int(getattr(now, "hour", 0)),
                minute=int(getattr(now, "minute", 0)),
                second=int(getattr(now, "second", 0)),
                microsecond=0,
            )
        current = now or datetime.now(self._nightly_timezone)
        if current.tzinfo is None:
            return current.replace(tzinfo=self._nightly_timezone)
        return current.astimezone(self._nightly_timezone)

    def _resolve_nightly_window(self, current: datetime) -> _NightlyWindow | None:
        windows = self._candidate_nightly_windows(current)
        for window in windows:
            if window.start_at <= current < window.end_at:
                return window
        recent = max(
            (window for window in windows if window.end_at <= current),
            key=lambda window: window.end_at,
            default=None,
        )
        if recent is None:
            return None
        catch_up_deadline = recent.end_at + timedelta(
            minutes=max(0, self._settings.nightly_catch_up_grace_minutes)
        )
        if current <= catch_up_deadline:
            return _NightlyWindow(
                start_at=recent.start_at,
                end_at=recent.end_at,
                catch_up=True,
            )
        return None

    def _candidate_nightly_windows(self, current: datetime) -> list[_NightlyWindow]:
        candidates: list[_NightlyWindow] = []
        current_date = current.date()
        start_hour = self._settings.nightly_start_hour_local
        end_hour = self._settings.nightly_end_hour_local
        for offset in (-1, 0, 1):
            start_date = current_date + timedelta(days=offset)
            start_at = datetime.combine(
                start_date,
                datetime_time(hour=start_hour),
                tzinfo=self._nightly_timezone,
            )
            if start_hour == end_hour:
                end_at = start_at + timedelta(days=1)
            elif start_hour < end_hour:
                end_at = datetime.combine(
                    start_date,
                    datetime_time(hour=end_hour),
                    tzinfo=self._nightly_timezone,
                )
            else:
                end_at = datetime.combine(
                    start_date + timedelta(days=1),
                    datetime_time(hour=end_hour),
                    tzinfo=self._nightly_timezone,
                )
            candidates.append(_NightlyWindow(start_at=start_at, end_at=end_at))
        return candidates

    async def _sanitize_restored_states(self) -> None:
        for state in await self._state_store.list_states():
            changed = False
            if state.dream_lock_owner is not None:
                state.dream_lock_owner = None
                changed = True
            self._sync_pending_flags(state)
            if changed:
                await self._state_store.put(state)

    @staticmethod
    def _sync_pending_flags(state: DreamSessionState) -> None:
        state.dream_pending = state.light_pending or state.nightly_pending

    def _has_active_worker(self, session_key: str) -> bool:
        if self._task_manager is None:
            return False
        # 覆盖两条执行路径的任务名：本地 spawn 的 dream-worker:* 与
        # dispatch consumer spawn 的 dream-dispatch:*（单进程部署共享 task_manager）
        return any(
            task.state == "running"
            and (task.name.startswith("dream-worker:") or task.name.startswith("dream-dispatch:"))
            for task in self._task_manager.list_tasks(category="dream", owner=session_key)
        )

    async def _should_scan_on_this_worker(self) -> bool:
        if self._multi_worker_scan_policy != "leader_only":
            return True
        registry = self._worker_registry
        if registry is None or not getattr(registry, "available", False):
            return True
        try:
            workers = await registry.active_workers(stale_threshold=self._worker_stale_threshold_s)
        except Exception:
            logger.warning("dream scan ownership resolution failed; falling back to local scan", exc_info=True)
            return True
        alive_ids = sorted(
            str(worker.get("id", "")).strip()
            for worker in workers
            if str(worker.get("status", "")).strip().lower() != "dead"
            and str(worker.get("id", "")).strip()
        )
        if not alive_ids:
            return True
        return str(getattr(registry, "worker_id", "")).strip() == alive_ids[0]

    def _nightly_window_from_job(self, job: DreamDispatchJob) -> _NightlyWindow | None:
        if job.nightly_window_start_at is None:
            return None
        start_at = datetime.fromtimestamp(job.nightly_window_start_at, tz=self._nightly_timezone)
        if job.nightly_window_end_at is None:
            end_at = start_at
        else:
            end_at = datetime.fromtimestamp(job.nightly_window_end_at, tz=self._nightly_timezone)
        return _NightlyWindow(start_at=start_at, end_at=end_at)


def _resolve_nightly_timezone(raw_timezone: str) -> datetime.tzinfo:
    normalized = raw_timezone.strip() if raw_timezone else "local"
    if not normalized or normalized.lower() == "local":
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is None:
            raise ValueError("failed to resolve local timezone for DreamScheduler")
        return local_tz
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid nightly timezone: {normalized}") from exc
