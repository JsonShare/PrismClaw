"""Process-wide service state: shared HTTP client, cache and Tavily key pool."""

from __future__ import annotations

import httpx

from ..config import Settings
from .cache import TTLCache
from .keypool import TavilyKeyPool

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class ServiceState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.total_timeout_s, connect=10.0),
            headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
            trust_env=True,  # honour HTTP(S)_PROXY env in offline / proxied networks
        )
        self.cache = TTLCache(
            ttl_seconds=settings.cache_ttl_seconds,
            max_size=settings.cache_max_size,
        )
        self.tavily_pool = TavilyKeyPool(settings.tavily_keys)

    async def aclose(self) -> None:
        await self.client.aclose()


_state: ServiceState | None = None


def init_state(settings: Settings | None = None) -> ServiceState:
    global _state
    if _state is None:
        _state = ServiceState(settings or Settings())
    return _state


def get_state() -> ServiceState:
    if _state is None:
        return init_state()
    return _state


async def close_state() -> None:
    global _state
    if _state is not None:
        await _state.aclose()
        _state = None
