from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

logger = logging.getLogger(__name__)

DreamDispatchKind = Literal["light", "nightly"]


@dataclass(slots=True)
class DreamDispatchJob:
    stream_id: str
    session_key: str
    job_kind: DreamDispatchKind
    enqueued_at: float
    nightly_window_start_at: float | None = None
    nightly_window_end_at: float | None = None
    attempt_count: int = 0
    last_error: str | None = None
    retry_at: float | None = None


class RedisDreamDispatchQueue:
    def __init__(
        self,
        redis_client: Any,
        *,
        key_prefix: str = "prismclaw:dream:dispatch:",
        group_name: str = "dream-dispatch",
        dedupe_ttl_seconds: int = 1800,
        max_retries: int = 2,
        retry_base_delay_seconds: float = 5.0,
        group_rebuild_cooldown_s: float = 30.0,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._redis = redis_client
        self._stream_key = f"{key_prefix}stream"
        self._dead_letter_key = f"{key_prefix}dead-letter"
        self._audit_key = f"{key_prefix}audit"
        self._group_name = group_name
        self._dedupe_key_prefix = f"{key_prefix}pending:"
        self._dedupe_ttl_seconds = max(60, dedupe_ttl_seconds)
        self._max_retries = max(0, int(max_retries))
        self._retry_base_delay_seconds = max(0.0, float(retry_base_delay_seconds))
        self._group_rebuild_cooldown_s = max(0.0, float(group_rebuild_cooldown_s))
        self._time_fn = time_fn or time.time
        self._group_ready = False
        self._last_group_rebuild_at: float | None = None
        self._supports_xautoclaim: bool | None = None

    async def ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._redis.xgroup_create(
                self._stream_key,
                self._group_name,
                id="0-0",
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def enqueue(self, job: DreamDispatchJob) -> bool:
        marker_key = self._marker_key(job)
        acquired = await self._redis.set(
            marker_key,
            str(job.enqueued_at),
            nx=True,
            ex=self._dedupe_ttl_seconds,
        )
        if not acquired:
            return False
        try:
            await self.ensure_group()
            await self._redis.xadd(
                self._stream_key,
                {
                    "session_key": job.session_key,
                    "job_kind": job.job_kind,
                    "enqueued_at": str(job.enqueued_at),
                    "nightly_window_start_at": _serialize_optional_float(job.nightly_window_start_at),
                    "nightly_window_end_at": _serialize_optional_float(job.nightly_window_end_at),
                    "attempt_count": str(int(job.attempt_count)),
                    "last_error": job.last_error or "",
                    "retry_at": _serialize_optional_float(job.retry_at),
                },
            )
            await self._record_audit("enqueued", job)
            return True
        except Exception:
            await self._redis.delete(marker_key)
            raise

    async def replay_dead_letter(self, dead_letter_stream_id: str) -> dict[str, Any]:
        entry = await self._get_dead_letter_entry(dead_letter_stream_id)
        if entry is None:
            return {"status": "not_found"}
        session_key = str(entry.get("session_key", "") or "").strip()
        job_kind = str(entry.get("job_kind", "") or "").strip().lower()
        if not session_key or job_kind not in {"light", "nightly"}:
            return {"status": "invalid_dead_letter"}
        replay_job = DreamDispatchJob(
            stream_id="",
            session_key=session_key,
            job_kind=job_kind,  # type: ignore[arg-type]
            enqueued_at=self._time_fn(),
            nightly_window_start_at=_parse_optional_float(entry.get("nightly_window_start_at")),
            nightly_window_end_at=_parse_optional_float(entry.get("nightly_window_end_at")),
            attempt_count=0,
            last_error=_parse_optional_text(entry.get("last_error")),
            retry_at=None,
        )
        marker_key = self._marker_key(replay_job)
        acquired = await self._redis.set(
            marker_key,
            str(replay_job.enqueued_at),
            nx=True,
            ex=self._dedupe_ttl_seconds,
        )
        if not acquired:
            await self._record_audit(
                "replay_skipped_duplicate",
                replay_job,
                extra_fields={"dead_letter_stream_id": dead_letter_stream_id},
            )
            return {"status": "duplicate_pending"}
        try:
            pipe = self._redis.pipeline()
            pipe.xadd(
                self._stream_key,
                {
                    "session_key": replay_job.session_key,
                    "job_kind": replay_job.job_kind,
                    "enqueued_at": str(replay_job.enqueued_at),
                    "nightly_window_start_at": _serialize_optional_float(replay_job.nightly_window_start_at),
                    "nightly_window_end_at": _serialize_optional_float(replay_job.nightly_window_end_at),
                    "attempt_count": str(int(replay_job.attempt_count)),
                    "last_error": replay_job.last_error or "",
                    "retry_at": "",
                },
            )
            pipe.xdel(self._dead_letter_key, dead_letter_stream_id)
            results = await pipe.execute()
            replay_stream_id = str(results[0]) if results else ""
            await self._record_audit(
                "replayed",
                DreamDispatchJob(
                    stream_id=replay_stream_id,
                    session_key=replay_job.session_key,
                    job_kind=replay_job.job_kind,
                    enqueued_at=replay_job.enqueued_at,
                    nightly_window_start_at=replay_job.nightly_window_start_at,
                    nightly_window_end_at=replay_job.nightly_window_end_at,
                    attempt_count=replay_job.attempt_count,
                    last_error=replay_job.last_error,
                    retry_at=None,
                ),
                extra_fields={"dead_letter_stream_id": dead_letter_stream_id},
            )
            return {"status": "replayed", "stream_id": replay_stream_id}
        except Exception:
            await self._redis.delete(marker_key)
            raise

    async def read(
        self,
        consumer_name: str,
        *,
        count: int = 10,
        block_ms: int = 1000,
    ) -> list[DreamDispatchJob]:
        return await self._read_group(consumer_name, count=count, block_ms=block_ms)

    async def _read_group(
        self,
        consumer_name: str,
        *,
        count: int,
        block_ms: int,
    ) -> list[DreamDispatchJob]:
        """NOGROUP 自愈读取：Redis 清空/组被销毁后自动重建消费组并重试一次。

        语义前提（SDD D1, §5.4）：ack 已通过 xdel 删除已处理条目，stream 中
        残留的即未投递/未 ack 任务；重建组（id="0-0"）重投递即正确恢复，
        不丢失任务、不搞 PEL 回放（NOGROUP 时组与 PEL 已一并销毁）。
        """
        await self.ensure_group()
        response = await self._xreadgroup_raw(consumer_name, count=count, block_ms=block_ms)
        if response is None and await self._recover_group():
            response = await self._xreadgroup_raw(consumer_name, count=count, block_ms=block_ms)
        return _parse_stream_jobs(response or [])

    async def _xreadgroup_raw(
        self,
        consumer_name: str,
        *,
        count: int,
        block_ms: int,
    ) -> list[Any] | None:
        try:
            return await self._redis.xreadgroup(
                self._group_name,
                consumer_name,
                {self._stream_key: ">"},
                count=count,
                block=block_ms,
            )
        except Exception as exc:
            if _is_nogroup_error(exc):
                return None
            raise

    async def _recover_group(self) -> bool:
        """重建消费组。冷却窗口内至多重建一次，防风暴；记录重建审计。"""
        now = self._time_fn()
        if (
            self._last_group_rebuild_at is not None
            and now - self._last_group_rebuild_at < self._group_rebuild_cooldown_s
        ):
            logger.warning(
                "dream dispatch group rebuild suppressed by cooldown (%.0fs)",
                self._group_rebuild_cooldown_s,
            )
            return False
        self._group_ready = False
        self._last_group_rebuild_at = now
        try:
            await self.ensure_group()
        except Exception:
            logger.warning("dream dispatch group rebuild failed", exc_info=True)
            return False
        await self._record_group_rebuild()
        logger.warning("dream dispatch consumer group rebuilt for %s", self._stream_key)
        return True

    async def _record_group_rebuild(self) -> None:
        try:
            stream_len = int(await self._redis.xlen(self._stream_key) or 0)
        except Exception:
            stream_len = -1
        try:
            await self._redis.xadd(
                self._audit_key,
                {
                    "event": "group_rebuilt",
                    "session_key": "",
                    "job_kind": "",
                    "stream_id": "",
                    "enqueued_at": str(self._time_fn()),
                    "attempt_count": "0",
                    "last_error": "",
                    "retry_at": "",
                    "stream_len": str(stream_len),
                },
                maxlen=5000,
                approximate=True,
            )
        except Exception:
            logger.warning("dream dispatch group-rebuild audit failed", exc_info=True)

    async def reclaim_stale(
        self,
        consumer_name: str,
        *,
        min_idle_ms: int,
        count: int = 10,
    ) -> list[DreamDispatchJob]:
        if min_idle_ms <= 0:
            return []
        if self._supports_xautoclaim is False:
            return []
        if not hasattr(self._redis, "xautoclaim"):
            self._supports_xautoclaim = False
            return []
        await self.ensure_group()
        try:
            raw = await self._redis.xautoclaim(
                self._stream_key,
                self._group_name,
                consumer_name,
                min_idle_ms,
                "0-0",
                count=count,
            )
            self._supports_xautoclaim = True
        except Exception as exc:
            if _is_nogroup_error(exc):
                # 组被销毁 → 自愈重建后重试一次（与 read 同语义）。
                if await self._recover_group():
                    try:
                        raw = await self._redis.xautoclaim(
                            self._stream_key,
                            self._group_name,
                            consumer_name,
                            min_idle_ms,
                            "0-0",
                            count=count,
                        )
                        self._supports_xautoclaim = True
                    except Exception:
                        logger.warning(
                            "dream dispatch stale reclaim retry failed", exc_info=True
                        )
                        return []
                else:
                    return []
            elif _is_unknown_command_error(exc, "XAUTOCLAIM"):
                self._supports_xautoclaim = False
                logger.warning(
                    "dream dispatch stale reclaim disabled because Redis server does not support XAUTOCLAIM"
                )
                return []
            else:
                logger.warning("dream dispatch stale reclaim failed", exc_info=True)
                return []
        messages: list[Any]
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            messages = raw[1]
        else:
            messages = []
        return _parse_claimed_jobs(messages)

    async def ack(self, job: DreamDispatchJob) -> None:
        pipe = self._redis.pipeline()
        pipe.xack(self._stream_key, self._group_name, job.stream_id)
        pipe.xdel(self._stream_key, job.stream_id)
        pipe.delete(self._marker_key(job))
        try:
            await pipe.execute()
        except Exception as exc:
            # 组在 claim 与 ack 之间被销毁 → 条目已随组丢失/将在重建后被
            # "0-0" 重投递，幂等重跑是预期行为；此处只降级审计，不让
            # 晚到的 ack 把已完成的 handler 打成失败。
            if _is_nogroup_error(exc):
                await self._record_audit("ack_group_missing", job)
                return
            raise
        await self._record_audit("succeeded", job)

    async def fail(self, job: DreamDispatchJob, *, reason: str | None = None) -> dict[str, Any]:
        try:
            return await self._fail_inner(job, reason=reason)
        except Exception as exc:
            if _is_nogroup_error(exc):
                # 同上：组已重建，本条目按 "0-0" 重投递幂等重跑。
                await self._record_audit("fail_group_missing", job)
                return {"status": "group_missing_redeliverable"}
            raise

    async def _fail_inner(self, job: DreamDispatchJob, *, reason: str | None = None) -> dict[str, Any]:
        next_attempt = int(job.attempt_count) + 1
        normalized_reason = _normalize_error_message(reason)
        if next_attempt <= self._max_retries:
            retry_at = self._time_fn() + self._compute_retry_delay_seconds(next_attempt)
            retry_job = DreamDispatchJob(
                stream_id="",
                session_key=job.session_key,
                job_kind=job.job_kind,
                enqueued_at=self._time_fn(),
                nightly_window_start_at=job.nightly_window_start_at,
                nightly_window_end_at=job.nightly_window_end_at,
                attempt_count=next_attempt,
                last_error=normalized_reason,
                retry_at=retry_at,
            )
            pipe = self._redis.pipeline()
            pipe.xadd(
                self._stream_key,
                {
                    "session_key": retry_job.session_key,
                    "job_kind": retry_job.job_kind,
                    "enqueued_at": str(retry_job.enqueued_at),
                    "nightly_window_start_at": _serialize_optional_float(retry_job.nightly_window_start_at),
                    "nightly_window_end_at": _serialize_optional_float(retry_job.nightly_window_end_at),
                    "attempt_count": str(int(retry_job.attempt_count)),
                    "last_error": retry_job.last_error or "",
                    "retry_at": _serialize_optional_float(retry_job.retry_at),
                },
            )
            pipe.xack(self._stream_key, self._group_name, job.stream_id)
            pipe.xdel(self._stream_key, job.stream_id)
            await pipe.execute()
            await self._record_audit("retry_scheduled", retry_job)
            return {"status": "retried", "attempt_count": next_attempt, "retry_at": retry_at}

        pipe = self._redis.pipeline()
        pipe.xadd(
            self._dead_letter_key,
            {
                "session_key": job.session_key,
                "job_kind": job.job_kind,
                "enqueued_at": str(job.enqueued_at),
                "attempt_count": str(next_attempt),
                "last_error": normalized_reason or "",
                "nightly_window_start_at": _serialize_optional_float(job.nightly_window_start_at),
                "nightly_window_end_at": _serialize_optional_float(job.nightly_window_end_at),
                "retry_at": _serialize_optional_float(job.retry_at),
            },
        )
        pipe.xack(self._stream_key, self._group_name, job.stream_id)
        pipe.xdel(self._stream_key, job.stream_id)
        pipe.delete(self._marker_key(job))
        await pipe.execute()
        await self._record_audit(
            "dead_lettered",
            DreamDispatchJob(
                stream_id=job.stream_id,
                session_key=job.session_key,
                job_kind=job.job_kind,
                enqueued_at=job.enqueued_at,
                nightly_window_start_at=job.nightly_window_start_at,
                nightly_window_end_at=job.nightly_window_end_at,
                attempt_count=next_attempt,
                last_error=normalized_reason,
                retry_at=job.retry_at,
            ),
        )
        return {"status": "dead_lettered", "attempt_count": next_attempt}

    async def stats(self) -> dict[str, Any]:
        try:
            await self.ensure_group()
            stream_length = int(await self._redis.xlen(self._stream_key) or 0)
            dead_letter_length = int(await self._redis.xlen(self._dead_letter_key) or 0)
            audit_length = int(await self._redis.xlen(self._audit_key) or 0)
            oldest_dead_letter_at = await self._stream_edge_time(self._dead_letter_key, newest=False)
            newest_dead_letter_at = await self._stream_edge_time(self._dead_letter_key, newest=True)
            pending_summary = await self._redis.xpending(self._stream_key, self._group_name)
            consumers_raw = []
            if hasattr(self._redis, "xinfo_consumers"):
                consumers_raw = await self._redis.xinfo_consumers(
                    self._stream_key,
                    self._group_name,
                )
            return {
                "available": True,
                "stream_key": self._stream_key,
                "group_name": self._group_name,
                "stream_length": stream_length,
                "dead_letter_length": dead_letter_length,
                "audit_length": audit_length,
                "oldest_dead_letter_at": oldest_dead_letter_at,
                "newest_dead_letter_at": newest_dead_letter_at,
                "pending_count": _parse_pending_count(pending_summary),
                "pending_min_id": _parse_pending_id(pending_summary, index=1),
                "pending_max_id": _parse_pending_id(pending_summary, index=2),
                "consumer_count": len(consumers_raw or []),
                "consumers": [_normalize_consumer_info(item) for item in consumers_raw or []],
                "dedupe_ttl_seconds": self._dedupe_ttl_seconds,
                "max_retries": self._max_retries,
                "retry_base_delay_seconds": self._retry_base_delay_seconds,
            }
        except Exception:
            logger.warning("dream dispatch stats failed", exc_info=True)
            return {
                "available": False,
                "stream_key": self._stream_key,
                "group_name": self._group_name,
                "stream_length": 0,
                "dead_letter_length": 0,
                "audit_length": 0,
                "oldest_dead_letter_at": None,
                "newest_dead_letter_at": None,
                "pending_count": 0,
                "pending_min_id": None,
                "pending_max_id": None,
                "consumer_count": 0,
                "consumers": [],
                "dedupe_ttl_seconds": self._dedupe_ttl_seconds,
                "max_retries": self._max_retries,
                "retry_base_delay_seconds": self._retry_base_delay_seconds,
            }

    async def metrics(self, *, limit: int = 200) -> dict[str, Any]:
        stats = await self.stats()
        if not bool(stats.get("available")):
            return {
                **stats,
                "summary": {
                    "health_status": "unavailable",
                    "activity_state": "unavailable",
                    "queue_backlog": 0,
                    "blocked_replay_count": 0,
                    "replayable_dead_letter_count": 0,
                },
                "dead_letter_metrics": {
                    "total_count": 0,
                    "sample_size": 0,
                    "replayable_count": 0,
                    "blocked_count": 0,
                    "latest_error": None,
                    "latest_session_key": None,
                    "oldest_age_seconds": None,
                    "newest_age_seconds": None,
                },
                "audit_metrics": {
                    "sample_size": 0,
                    "events_by_type": {},
                    "events_by_kind": {},
                    "latest_event": None,
                    "latest_session_key": None,
                },
                "replay_metrics": {
                    "candidate_count": 0,
                    "replayable_count": 0,
                    "blocked_count": 0,
                    "blocked_reasons": {},
                },
            }

        bounded_limit = max(1, limit)
        dead_letters = await self.list_dead_letters(limit=bounded_limit)
        replay_candidates = await self._build_replay_candidates(dead_letters)
        audit_events = await self.list_audit_events(limit=bounded_limit)
        replayable_count = len([item for item in replay_candidates if item.get("replayable") is True])
        blocked_count = len(replay_candidates) - replayable_count
        queue_backlog = int(stats.get("stream_length", 0) or 0) + int(stats.get("pending_count", 0) or 0)
        return {
            **stats,
            "summary": {
                "health_status": _dispatch_health_status(stats, blocked_count=blocked_count),
                "activity_state": _dispatch_activity_state(stats),
                "queue_backlog": queue_backlog,
                "blocked_replay_count": blocked_count,
                "replayable_dead_letter_count": replayable_count,
            },
            "dead_letter_metrics": {
                "total_count": int(stats.get("dead_letter_length", 0) or 0),
                "sample_size": len(dead_letters),
                "replayable_count": replayable_count,
                "blocked_count": blocked_count,
                "latest_error": _first_present(dead_letters, "last_error"),
                "latest_session_key": _first_present(dead_letters, "session_key"),
                "oldest_age_seconds": _age_seconds(
                    stats.get("oldest_dead_letter_at"),
                    now=self._time_fn(),
                ),
                "newest_age_seconds": _age_seconds(
                    stats.get("newest_dead_letter_at"),
                    now=self._time_fn(),
                ),
            },
            "audit_metrics": {
                "sample_size": len(audit_events),
                "events_by_type": _count_values(audit_events, "event"),
                "events_by_kind": _count_values(audit_events, "job_kind"),
                "latest_event": _first_present(audit_events, "event"),
                "latest_session_key": _first_present(audit_events, "session_key"),
            },
            "replay_metrics": {
                "candidate_count": len(replay_candidates),
                "replayable_count": replayable_count,
                "blocked_count": blocked_count,
                "blocked_reasons": _count_values(
                    replay_candidates,
                    "replay_block_reason",
                    skip_empty=True,
                ),
            },
        }

    async def list_dead_letters(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self._list_dead_letter_items(session_key=session_key, limit=limit)

    async def list_replay_candidates(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        items = await self._list_dead_letter_items(session_key=session_key, limit=limit)
        return await self._build_replay_candidates(items)

    async def replay_dead_letters(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        candidates = await self.list_replay_candidates(session_key=session_key, limit=limit)
        replayable = [item for item in candidates if item.get("replayable") is True]
        if dry_run:
            return {
                "status": "dry_run",
                "requested_count": len(candidates),
                "replayable_count": len(replayable),
                "blocked_count": len(candidates) - len(replayable),
                "items": candidates,
            }
        replayed: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for item in candidates:
            if item.get("replayable") is not True:
                blocked.append(
                    {
                        "dead_letter_stream_id": item.get("stream_id"),
                        "status": "duplicate_pending",
                    }
                )
                continue
            result = await self.replay_dead_letter(str(item.get("stream_id", "") or ""))
            replayed.append(
                {
                    "dead_letter_stream_id": item.get("stream_id"),
                    **result,
                }
            )
        return {
            "status": "replayed_batch",
            "requested_count": len(candidates),
            "replayed_count": len([item for item in replayed if item.get("status") == "replayed"]),
            "blocked_count": len(blocked),
            "items": [*replayed, *blocked],
        }

    async def _list_dead_letter_items(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        try:
            raw = await self._redis.xrevrange(self._dead_letter_key, count=max(1, limit))
        except Exception:
            logger.warning("dream dispatch dead-letter query failed", exc_info=True)
            return []
        items: list[dict[str, Any]] = []
        for item in raw or []:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            stream_id, fields = item
            if not isinstance(fields, dict):
                continue
            current_session_key = str(fields.get("session_key", "") or "").strip()
            if session_key is not None and current_session_key != session_key:
                continue
            items.append(
                {
                    "stream_id": str(stream_id),
                    "session_key": current_session_key,
                    "job_kind": str(fields.get("job_kind", "") or "").strip().lower(),
                    "enqueued_at": _parse_float(fields.get("enqueued_at"), default=0.0),
                    "attempt_count": _parse_int(fields.get("attempt_count"), default=0),
                    "last_error": _parse_optional_text(fields.get("last_error")),
                    "nightly_window_start_at": _parse_optional_float(fields.get("nightly_window_start_at")),
                    "nightly_window_end_at": _parse_optional_float(fields.get("nightly_window_end_at")),
                    "retry_at": _parse_optional_float(fields.get("retry_at")),
                }
            )
            if len(items) >= limit:
                break
        return items

    async def list_audit_events(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        try:
            raw = await self._redis.xrevrange(self._audit_key, count=max(1, limit))
        except Exception:
            logger.warning("dream dispatch audit query failed", exc_info=True)
            return []
        items: list[dict[str, Any]] = []
        for item in raw or []:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            stream_id, fields = item
            if not isinstance(fields, dict):
                continue
            current_session_key = str(fields.get("session_key", "") or "").strip()
            if session_key is not None and current_session_key != session_key:
                continue
            items.append(
                {
                    "stream_id": str(stream_id),
                    "event": str(fields.get("event", "") or "").strip(),
                    "session_key": current_session_key,
                    "job_kind": str(fields.get("job_kind", "") or "").strip().lower(),
                    "job_stream_id": str(fields.get("stream_id", "") or "").strip(),
                    "enqueued_at": _parse_float(fields.get("enqueued_at"), default=0.0),
                    "attempt_count": _parse_int(fields.get("attempt_count"), default=0),
                    "last_error": _parse_optional_text(fields.get("last_error")),
                    "retry_at": _parse_optional_float(fields.get("retry_at")),
                    "dead_letter_stream_id": _parse_optional_text(fields.get("dead_letter_stream_id")),
                }
            )
            if len(items) >= limit:
                break
        return items

    def _marker_key(self, job: DreamDispatchJob) -> str:
        return f"{self._dedupe_key_prefix}{job.job_kind}:{job.session_key}"

    def _compute_retry_delay_seconds(self, attempt_count: int) -> float:
        if self._retry_base_delay_seconds <= 0:
            return 0.0
        return self._retry_base_delay_seconds * float(2 ** max(0, attempt_count - 1))

    async def _record_audit(
        self,
        event: str,
        job: DreamDispatchJob,
        *,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        try:
            payload = {
                "event": event,
                "session_key": job.session_key,
                "job_kind": job.job_kind,
                "stream_id": job.stream_id,
                "enqueued_at": str(job.enqueued_at),
                "attempt_count": str(int(job.attempt_count)),
                "last_error": job.last_error or "",
                "retry_at": _serialize_optional_float(job.retry_at),
            }
            if extra_fields:
                for key, value in extra_fields.items():
                    payload[str(key)] = "" if value is None else str(value)
            await self._redis.xadd(
                self._audit_key,
                payload,
                maxlen=5000,
                approximate=True,
            )
        except Exception:
            logger.warning("dream dispatch audit record failed for %s", event, exc_info=True)

    async def _get_dead_letter_entry(self, dead_letter_stream_id: str) -> dict[str, Any] | None:
        try:
            raw = await self._redis.xrange(
                self._dead_letter_key,
                min=dead_letter_stream_id,
                max=dead_letter_stream_id,
                count=1,
            )
        except Exception:
            logger.warning("dream dispatch dead-letter lookup failed", exc_info=True)
            return None
        if not raw:
            return None
        item = raw[0]
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        _stream_id, fields = item
        if not isinstance(fields, dict):
            return None
        return dict(fields)

    async def _stream_edge_time(self, key: str, *, newest: bool) -> float | None:
        try:
            if newest:
                raw = await self._redis.xrevrange(key, count=1)
            else:
                raw = await self._redis.xrange(key, count=1)
        except Exception:
            return None
        if not raw:
            return None
        item = raw[0]
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        _stream_id, fields = item
        if not isinstance(fields, dict):
            return None
        return _parse_float(fields.get("enqueued_at"), default=0.0) or None

    async def _has_pending_marker_for_item(self, item: dict[str, Any]) -> bool:
        session_key = str(item.get("session_key", "") or "").strip()
        job_kind = str(item.get("job_kind", "") or "").strip().lower()
        if not session_key or job_kind not in {"light", "nightly"}:
            return True
        marker_key = f"{self._dedupe_key_prefix}{job_kind}:{session_key}"
        try:
            return bool(await self._redis.exists(marker_key))
        except Exception:
            logger.warning("dream dispatch replay eligibility check failed", exc_info=True)
            return True

    async def _build_replay_candidates(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in items:
            replayable = not await self._has_pending_marker_for_item(item)
            results.append(
                {
                    **item,
                    "replayable": replayable,
                    "replay_block_reason": None if replayable else "duplicate_pending",
                }
            )
        return results


class DreamDispatchConsumer:
    def __init__(
        self,
        *,
        queue: RedisDreamDispatchQueue,
        task_manager: Any,
        consumer_name: str,
        process_job: Callable[[DreamDispatchJob], Awaitable[bool]],
        reclaim_idle_ms: int = 120_000,
        read_block_ms: int = 1000,
        time_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._queue = queue
        self._task_manager = task_manager
        self._consumer_name = consumer_name
        self._process_job = process_job
        self._reclaim_idle_ms = max(0, reclaim_idle_ms)
        self._read_block_ms = max(100, read_block_ms)
        self._time_fn = time_fn or time.time
        self._sleep_fn = sleep_fn or asyncio.sleep

    async def start(self) -> None:
        await self._queue.ensure_group()
        try:
            while True:
                reclaimed = await self._queue.reclaim_stale(
                    self._consumer_name,
                    min_idle_ms=self._reclaim_idle_ms,
                    count=10,
                )
                if reclaimed:
                    self._dispatch_jobs(reclaimed)
                    continue
                jobs = await self._queue.read(
                    self._consumer_name,
                    count=10,
                    block_ms=self._read_block_ms,
                )
                if not jobs:
                    await asyncio.sleep(0)
                    continue
                self._dispatch_jobs(jobs)
        except asyncio.CancelledError:
            return

    def _dispatch_jobs(self, jobs: list[DreamDispatchJob]) -> None:
        for job in jobs:
            self._task_manager.spawn(
                f"dream-dispatch:{job.job_kind}:{job.session_key}",
                self._process_and_finalize(job),
                category="dream",
                owner=job.session_key,
            )

    async def _process_and_finalize(self, job: DreamDispatchJob) -> None:
        ok = False
        failure_reason: str | None = None
        await self._wait_until_retry_ready(job)
        await self._queue._record_audit("claimed", job)
        try:
            ok = await self._process_job(job)
        except Exception as exc:
            failure_reason = str(exc)
            logger.warning("dream dispatch job failed for %s", job.session_key, exc_info=True)
        finally:
            if ok:
                await self._queue.ack(job)
            else:
                await self._queue.fail(job, reason=failure_reason)

    async def _wait_until_retry_ready(self, job: DreamDispatchJob) -> None:
        if job.retry_at is None:
            return
        delay = float(job.retry_at) - float(self._time_fn())
        if delay > 0:
            await self._sleep_fn(delay)


def build_light_dispatch_job(session_key: str, *, enqueued_at: float | None = None) -> DreamDispatchJob:
    return DreamDispatchJob(
        stream_id="",
        session_key=session_key,
        job_kind="light",
        enqueued_at=float(enqueued_at or time.time()),
    )


def build_nightly_dispatch_job(
    session_key: str,
    *,
    nightly_window_start_at: float,
    nightly_window_end_at: float,
    enqueued_at: float | None = None,
) -> DreamDispatchJob:
    return DreamDispatchJob(
        stream_id="",
        session_key=session_key,
        job_kind="nightly",
        enqueued_at=float(enqueued_at or time.time()),
        nightly_window_start_at=nightly_window_start_at,
        nightly_window_end_at=nightly_window_end_at,
    )


def _serialize_optional_float(value: float | None) -> str:
    return "" if value is None else str(float(value))


def _parse_stream_jobs(raw: list[Any]) -> list[DreamDispatchJob]:
    jobs: list[DreamDispatchJob] = []
    for item in raw or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        _stream_name, messages = item
        jobs.extend(_parse_claimed_jobs(messages))
    return jobs


def _parse_claimed_jobs(messages: list[Any]) -> list[DreamDispatchJob]:
    jobs: list[DreamDispatchJob] = []
    for message in messages or []:
        if not isinstance(message, (list, tuple)) or len(message) != 2:
            continue
        stream_id, fields = message
        if not isinstance(fields, dict):
            continue
        session_key = str(fields.get("session_key", "") or "").strip()
        job_kind = str(fields.get("job_kind", "") or "").strip().lower()
        if not session_key or job_kind not in {"light", "nightly"}:
            continue
        jobs.append(
            DreamDispatchJob(
                stream_id=str(stream_id),
                session_key=session_key,
                job_kind=job_kind,  # type: ignore[arg-type]
                enqueued_at=_parse_float(fields.get("enqueued_at"), default=0.0),
                nightly_window_start_at=_parse_optional_float(fields.get("nightly_window_start_at")),
                nightly_window_end_at=_parse_optional_float(fields.get("nightly_window_end_at")),
                attempt_count=_parse_int(fields.get("attempt_count"), default=0),
                last_error=_parse_optional_text(fields.get("last_error")),
                retry_at=_parse_optional_float(fields.get("retry_at")),
            )
        )
    return jobs


def _is_unknown_command_error(exc: Exception, command_name: str) -> bool:
    message = str(exc).lower()
    return "unknown command" in message and command_name.lower() in message


def _is_nogroup_error(exc: Exception) -> bool:
    return "nogroup" in str(exc).lower()


def _parse_float(raw: Any, *, default: float) -> float:
    try:
        return float(raw)
    except Exception:
        return default


def _parse_optional_float(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _parse_int(raw: Any, *, default: int) -> int:
    try:
        return int(raw)
    except Exception:
        return default


def _parse_optional_text(raw: Any) -> str | None:
    text = str(raw or "").strip()
    return text or None


def _parse_pending_count(raw: Any) -> int:
    if isinstance(raw, dict):
        return int(raw.get("pending", 0) or 0)
    if isinstance(raw, (list, tuple)) and raw:
        try:
            return int(raw[0] or 0)
        except Exception:
            return 0
    return 0


def _parse_pending_id(raw: Any, *, index: int) -> str | None:
    if isinstance(raw, dict):
        key = "min" if index == 1 else "max"
        value = raw.get(key)
        return str(value) if value else None
    if isinstance(raw, (list, tuple)) and len(raw) > index:
        value = raw[index]
        return str(value) if value else None
    return None


def _normalize_consumer_info(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        name = raw.get("name")
        pending = raw.get("pending")
        idle = raw.get("idle")
    elif isinstance(raw, (list, tuple)):
        name = raw[0] if len(raw) > 0 else None
        pending = raw[1] if len(raw) > 1 else None
        idle = raw[2] if len(raw) > 2 else None
    else:
        name = pending = idle = None
    return {
        "name": str(name) if name is not None else "",
        "pending": int(pending or 0),
        "idle_ms": int(idle or 0),
    }


def _normalize_error_message(reason: str | None) -> str | None:
    text = str(reason or "").strip()
    if not text:
        return None
    return text[:500]


def _first_present(items: list[dict[str, Any]], key: str) -> Any:
    for item in items:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _count_values(
    items: list[dict[str, Any]],
    key: str,
    *,
    skip_empty: bool = False,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = item.get(key)
        if value in (None, "") and skip_empty:
            continue
        normalized = str(value or "").strip()
        if not normalized and skip_empty:
            continue
        label = normalized or "unknown"
        counts[label] = counts.get(label, 0) + 1
    return counts


def _age_seconds(raw: Any, *, now: float) -> float | None:
    timestamp = _parse_optional_float(raw)
    if timestamp is None:
        return None
    return max(0.0, float(now) - float(timestamp))


def _dispatch_health_status(stats: dict[str, Any], *, blocked_count: int) -> str:
    if not bool(stats.get("available")):
        return "unavailable"
    dead_letter_length = int(stats.get("dead_letter_length", 0) or 0)
    if dead_letter_length > 0 or blocked_count > 0:
        return "degraded"
    return "healthy"


def _dispatch_activity_state(stats: dict[str, Any]) -> str:
    if not bool(stats.get("available")):
        return "unavailable"
    stream_length = int(stats.get("stream_length", 0) or 0)
    pending_count = int(stats.get("pending_count", 0) or 0)
    if stream_length > 0 or pending_count > 0:
        return "busy"
    return "idle"
