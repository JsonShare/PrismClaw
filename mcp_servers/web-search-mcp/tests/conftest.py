from __future__ import annotations

import pytest

from web_search_mcp.config import Settings
from web_search_mcp.infra.runtime import ServiceState


@pytest.fixture
def settings() -> Settings:
    return Settings(
        token="test-token",
        tavily_api_keys="test-key",
        anysearch_api_key="",
        allowed_schemes="https",
        allowed_domains="",
        total_timeout_s=5.0,
        search_timeout_s=1.0,
        fetch_timeout_s=1.0,
        max_fetch_pages=3,
        max_per_host=2,
        cache_ttl_seconds=0,
    )


@pytest.fixture(autouse=True)
def _no_real_api_keys(monkeypatch):
    """Ensure tests never use real API keys from .env file."""
    from web_search_mcp.server import app as app_mod
    monkeypatch.setattr(app_mod.settings, "tavily_api_keys", "test-key")
    monkeypatch.setattr(app_mod.settings, "anysearch_api_key", "")
    from web_search_mcp.infra import runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "_state", None)
    yield


@pytest.fixture
async def state(settings: Settings) -> ServiceState:
    svc = ServiceState(settings)
    try:
        yield svc
    finally:
        await svc.aclose()
