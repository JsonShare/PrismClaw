from __future__ import annotations

import logging
import time
from typing import Any

from prismclaw.engine.dream.dispatch.boundary import DreamEnqueueResult
from prismclaw.schema.dream.dream import DreamStateStore
from prismclaw.schema.dream.dream_types import DreamSessionState

logger = logging.getLogger(__name__)


class NullDreamBoundary:
    def is_available(self) -> bool:
        return False

    async def mark_activity(self, session_key: str, *, dirty_delta: int = 1) -> DreamEnqueueResult:
        _ = dirty_delta
        return DreamEnqueueResult(
            status="unavailable",
            operation="mark_activity",
            session_key=session_key,
            detail="dream runtime unavailable",
        )

    async def enqueue_nightly(self, session_key: str) -> DreamEnqueueResult:
        return DreamEnqueueResult(
            status="unavailable",
            operation="enqueue_nightly",
            session_key=session_key,
            detail="dream runtime unavailable",
        )

    async def run_dispatched_job(self, _job: Any) -> bool:
        return False


class InProcessDreamBoundary:
    def __init__(
        self,
        *,
        scheduler: Any,
        review_service: Any,
        state_store: DreamStateStore | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._review_service = review_service
        self._state_store = state_store

    def is_available(self) -> bool:
        return self._review_service is not None or self._scheduler is not None

    async def mark_activity(self, session_key: str, *, dirty_delta: int = 1) -> DreamEnqueueResult:
        scheduler = self._scheduler
        if scheduler is None:
            result = DreamEnqueueResult(
                status="unavailable",
                operation="mark_activity",
                session_key=session_key,
                detail="scheduler unavailable",
            )
            await self._record_enqueue_result(result)
            return result
        fn = getattr(scheduler, "mark_activity", None)
        if not callable(fn):
            result = DreamEnqueueResult(
                status="unavailable",
                operation="mark_activity",
                session_key=session_key,
                detail="scheduler mark_activity unavailable",
            )
            await self._record_enqueue_result(result)
            return result
        try:
            await fn(session_key, dirty_delta=dirty_delta)
            return DreamEnqueueResult(
                status="accepted",
                operation="mark_activity",
                session_key=session_key,
            )
        except Exception:
            logger.warning("dream mark_activity failed for %s", session_key, exc_info=True)
            result = DreamEnqueueResult(
                status="failed",
                operation="mark_activity",
                session_key=session_key,
                detail="scheduler mark_activity failed",
            )
            await self._record_enqueue_result(result)
            return result

    async def enqueue_nightly(self, session_key: str) -> DreamEnqueueResult:
        scheduler = self._scheduler
        if scheduler is None:
            result = DreamEnqueueResult(
                status="unavailable",
                operation="enqueue_nightly",
                session_key=session_key,
                detail="scheduler unavailable",
            )
            await self._record_enqueue_result(result)
            return result
        fn = getattr(scheduler, "enqueue_nightly", None)
        if not callable(fn):
            result = DreamEnqueueResult(
                status="unavailable",
                operation="enqueue_nightly",
                session_key=session_key,
                detail="scheduler enqueue_nightly unavailable",
            )
            await self._record_enqueue_result(result)
            return result
        try:
            await fn(session_key)
            return DreamEnqueueResult(
                status="accepted",
                operation="enqueue_nightly",
                session_key=session_key,
            )
        except Exception:
            logger.warning("dream enqueue_nightly failed for %s", session_key, exc_info=True)
            result = DreamEnqueueResult(
                status="failed",
                operation="enqueue_nightly",
                session_key=session_key,
                detail="scheduler enqueue_nightly failed",
            )
            await self._record_enqueue_result(result)
            return result

    async def _record_enqueue_result(self, result: DreamEnqueueResult) -> None:
        """Persist an enqueue outcome that the scheduler did not persist itself.

        Only invoked on the unavailable/failed paths: when the scheduler accepts
        the enqueue it already writes ``last_enqueue_*_status='accepted'`` into
        the state store, so the success path returns without a redundant write.
        """
        store = self._state_store
        if store is None:
            return
        try:
            now = time.time()
            state = await store.get(result.session_key) or DreamSessionState(
                session_key=result.session_key
            )
            if result.operation == "mark_activity":
                state.last_enqueue_activity_at = now
                state.last_enqueue_activity_status = result.status
                state.last_enqueue_activity_detail = result.detail
            else:
                state.last_enqueue_nightly_at = now
                state.last_enqueue_nightly_status = result.status
                state.last_enqueue_nightly_detail = result.detail
            await store.put(state)
        except Exception:
            logger.warning("dream enqueue result record failed for %s", result.session_key, exc_info=True)

    async def run_dispatched_job(self, job: Any) -> bool:
        scheduler = self._scheduler
        if scheduler is None:
            return False
        run = getattr(scheduler, "run_dispatched_job", None)
        if not callable(run):
            return False
        try:
            return bool(await run(job))
        except Exception:
            logger.warning("dream dispatched job failed", exc_info=True)
            return False
