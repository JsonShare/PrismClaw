"""API key pool with cooldown backoff for rate-limited search providers.

Design for Tavily's free-tier monthly limit (1000 calls/month per key):

- **Concurrency control**: a ``Semaphore(len(keys))`` caps in-flight requests
  at the number of keys, so each key handles at most one concurrent request —
  staying within per-key QPS limits without manual rate tuning.
- **Round-robin rotation**: each ``acquire()`` returns the next available key,
  spreading load evenly across all configured keys.
- **Per-key cooldown**: on failure, the key enters a cooldown window whose
  duration depends on the error type:
    - 429 (rate limit): base 60s, exponentially backed off, capped at 1h.
      A key that hits monthly limits will rapidly escalate to the 1h cap
      and be skipped by ``acquire()`` for long stretches.
    - 5xx: base 20s, capped at 5min (transient server errors).
    - other: base 10s, capped at 5min.
- **Usage counting**: each successful call increments ``success_count``,
  enabling observability of per-key consumption without persistent state.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

# 429 (monthly/minute rate limit) backoff caps at 1 hour so an exhausted key
# is not retried every few minutes (wasting API calls on guaranteed 429s).
_RATE_LIMIT_BACKOFF_CAP = 3600.0
# Transient server errors cap at 5 minutes.
_SERVER_ERROR_BACKOFF_CAP = 300.0


@dataclass
class _KeyState:
    key: str
    fail_count: int = 0
    cooldown_until_ts: float = 0.0
    success_count: int = 0


class TavilyKeyPool:
    def __init__(self, keys: list[str]) -> None:
        self._states = [_KeyState(key=k) for k in keys]
        self._rr = 0
        # Cap in-flight requests at key count so round-robin assigns a
        # distinct key to each concurrent caller (per-key QPS safe).
        self._semaphore = asyncio.Semaphore(len(keys)) if keys else None

    def __len__(self) -> int:
        return len(self._states)

    def empty(self) -> bool:
        return not self._states

    async def acquire_slot(self) -> None:
        """Wait until a key slot is available (max concurrency = key count)."""
        if self._semaphore is not None:
            await self._semaphore.acquire()

    def release_slot(self) -> None:
        """Release a previously acquired slot."""
        if self._semaphore is not None:
            self._semaphore.release()

    def acquire(self) -> str | None:
        if not self._states:
            return None
        now = time.time()
        n = len(self._states)
        for i in range(n):
            idx = (self._rr + i) % n
            st = self._states[idx]
            if st.cooldown_until_ts <= now:
                self._rr = (idx + 1) % n
                return st.key
        idx = self._rr % n
        self._rr = (idx + 1) % n
        return self._states[idx].key

    def report_success(self, key: str) -> None:
        for st in self._states:
            if st.key == key:
                st.fail_count = 0
                st.cooldown_until_ts = 0.0
                st.success_count += 1
                return

    def report_failure(self, key: str, *, status_code: int | None = None) -> None:
        for st in self._states:
            if st.key != key:
                continue
            st.fail_count = min(st.fail_count + 1, 8)
            if status_code == 429:
                base = 60.0
                cap = _RATE_LIMIT_BACKOFF_CAP
            elif status_code is not None and 500 <= status_code < 600:
                base = 20.0
                cap = _SERVER_ERROR_BACKOFF_CAP
            else:
                base = 10.0
                cap = _SERVER_ERROR_BACKOFF_CAP
            backoff = min(base * (2 ** max(0, st.fail_count - 1)), cap)
            jitter = random.uniform(0.0, 0.25 * backoff)
            st.cooldown_until_ts = time.time() + backoff + jitter
            return

    def stats(self) -> list[dict[str, object]]:
        """Return per-key usage statistics for observability."""
        return [
            {
                "key": st.key[:12] + "...",
                "success_count": st.success_count,
                "fail_count": st.fail_count,
                "cooldown_remaining_s": max(0.0, st.cooldown_until_ts - time.time()),
            }
            for st in self._states
        ]
