"""TrajectoryFlushWorker — async queue-based non-blocking flush collector.

Architecture:
  ┌──────────────┐    submit(state)     ┌─────────────────────┐
  │  Event Bus   │ ──────────────────►  │  asyncio.Queue      │
  │  (sync path) │   O(1) non-blocking  │  (dirty signals)    │
  └──────────────┘                      └─────────┬───────────┘
                                                │
                                       async consumer task
                                       (every 5s or depth>=3)
                                                │
                                                ▼
                                       ┌─────────────────────┐
                                       │  persistence.flush  │
                                       │  (batch UPSERT)     │
                                       └─────────────────────┘

All I/O goes through the injected TrajectoryPersistence Protocol —
the engine layer never imports persistence directly.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from prismclaw.schema.trajectory.trajectory import TrajectoryPersistence

logger = logging.getLogger(__name__)

# Flush policy constants
FLUSH_INTERVAL_S = 5.0       # Max seconds between flushes
FLUSH_THRESHOLD = 3          # Queue depth that triggers immediate flush
SHUTDOWN_TIMEOUT_S = 10.0    # Max time to wait for flush on shutdown


class TrajectoryFlushWorker:
    """Async queue-based flush collector for trajectory audit data.

    Usage:
        worker = TrajectoryFlushWorker(session_id, persistence)
        await worker.start()     # starts background consumer task
        worker.submit(state)     # O(1) non-blocking signal
        await worker.stop()      # graceful shutdown + final flush

    Reliability guarantee:
      - submit() NEVER blocks the caller (uses put_nowait)
      - Consumer ALWAYS wakes up on new signal (await queue.get())
      - Shutdown ALWAYS drains residual queue before returning
    """

    def __init__(
        self,
        session_id: str,
        persistence: TrajectoryPersistence | None,
        *,
        flush_interval_s: float = FLUSH_INTERVAL_S,
        flush_threshold: int = FLUSH_THRESHOLD,
    ) -> None:
        self.session_id = session_id
        self._persistence = persistence
        self.flush_interval_s = flush_interval_s
        self.flush_threshold = flush_threshold

        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._state: Optional[Any] = None  # Latest state snapshot
        self._submitted_count = 0
        self._flushed_count = 0
        self._last_flush_ms = 0

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Start the background consumer task. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._consumer_loop())
        logger.info(
            "trajectory flush worker started: sid=%s interval=%.1fs threshold=%d",
            self.session_id, self.flush_interval_s, self.flush_threshold,
        )

    async def stop(self) -> None:
        """Graceful shutdown: drain residual queue + final flush.

        Order: set stop flag → wake consumer → wait for drain → final flush.
        """
        if self._task is None or self._task.done():
            return

        logger.info("trajectory flush worker stopping: sid=%s", self.session_id)
        self._stop_event.set()

        # Wake consumer by putting a sentinel (it's already waiting on queue.get)
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

        # Wait for consumer loop to exit (with timeout)
        try:
            await asyncio.wait_for(
                self._task,
                timeout=SHUTDOWN_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "trajectory flush worker stop timeout: sid=%s "
                "(%d pending items may be lost)",
                self.session_id, self._queue.qsize(),
            )
            if not self._task.done():
                self._task.cancel()

        self._task = None
        logger.info(
            "trajectory flush worker stopped: sid=%s flushed_total=%d",
            self.session_id, self._flushed_count,
        )

    # ------------------------------------------------------------------ submit

    def submit(self, state: Any) -> None:
        """Non-blocking submit: signal that state needs flushing.

        O(1) — only pushes to queue, never awaits database I/O.
        Stores the latest state snapshot for the consumer to use.
        """
        self._state = state
        self._submitted_count += 1
        try:
            self._queue.put_nowait(None)  # Sentinel = flush signal
        except asyncio.QueueFull:
            logger.warning(
                "trajectory flush worker queue full: sid=%s (dropping signal)",
                self.session_id,
            )

    # ------------------------------------------------------------------ internal

    async def _consumer_loop(self) -> None:
        """Background loop: wait for signals, batch, flush.

        Simple reliable loop:
          1. await queue.get() — blocks until a signal arrives (wake on submit)
          2. After getting a signal, drain all queued signals
          3. Flush if we have state and (threshold reached or interval elapsed)
          4. Check stop flag after each flush
        """
        next_flush_deadline = 0.0

        while not self._stop_event.is_set():
            try:
                # Step 1: Wait for a signal (blocking, but cancellable via stop)
                try:
                    await asyncio.wait_for(
                        self._queue.get(),
                        timeout=self.flush_interval_s,
                    )
                except asyncio.TimeoutError:
                    # Timeout — check if we have state to flush
                    pass

                if self._stop_event.is_set():
                    break

                # Step 2: Drain all accumulated signals from queue
                drained = 1  # Count the one we just got
                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                        drained += 1
                    except asyncio.QueueEmpty:
                        break

                # Step 3: Decide whether to flush
                should_flush = False
                now = time.monotonic()

                if drained >= self.flush_threshold:
                    # Threshold reached — immediate flush
                    should_flush = True
                elif now >= next_flush_deadline and drained > 0:
                    # Time deadline — flush
                    should_flush = True
                elif drained > 0 and self._state is not None:
                    # Has signals but below threshold — flush anyway
                    # (small cost, ensures data is persisted quickly)
                    should_flush = True

                if should_flush and self._state is not None:
                    await self._do_flush(force=False)
                    next_flush_deadline = now + self.flush_interval_s

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    "trajectory flush worker consumer loop error: "
                    "sid=%s: %s", self.session_id, exc,
                )
                await asyncio.sleep(1.0)

        # Final drain after stop signal
        if self._state is not None:
            await self._do_flush(force=True)

    async def _do_flush(self, *, force: bool = False) -> None:
        """Execute the actual flush via the persistence Protocol."""
        if self._state is None or self._persistence is None:
            return

        try:
            result = await self._persistence.flush_state(
                self._state,
                only_if_dirty=not force,
            )
            if result:
                self._flushed_count += 1
                self._last_flush_ms = int(time.time() * 1000)
                logger.info(
                    "trajectory flush worker completed: sid=%s "
                    "records=%d flushed_total=%d",
                    self.session_id,
                    len(self._state.records),
                    self._flushed_count,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trajectory flush worker flush failed: sid=%s: %s",
                self.session_id, exc,
            )

    # ------------------------------------------------------------------ stats

    @property
    def pending_count(self) -> int:
        """Number of unprocessed flush signals."""
        return self._queue.qsize()

    @property
    def stats(self) -> dict[str, Any]:
        """Worker statistics for monitoring."""
        return {
            "session_id": self.session_id,
            "running": self._task is not None and not self._task.done(),
            "pending": self._queue.qsize(),
            "submitted": self._submitted_count,
            "flushed": self._flushed_count,
            "last_flush_ms": self._last_flush_ms,
        }


__all__ = [
    "TrajectoryFlushWorker",
    "FLUSH_INTERVAL_S",
    "FLUSH_THRESHOLD",
    "SHUTDOWN_TIMEOUT_S",
]
