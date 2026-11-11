"""Small TTL + LRU cache (process-local), ported from the former builtin tool."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, *, ttl_seconds: float, max_size: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_size
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if item is None:
            return None
        ts, payload = item
        if self._ttl > 0 and (time.time() - ts) > self._ttl:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return payload

    def set(self, key: str, payload: Any) -> None:
        if self._ttl <= 0:
            return
        if key in self._store:
            del self._store[key]
        while len(self._store) >= self._max:
            self._store.popitem(last=False)
        self._store[key] = (time.time(), payload)
