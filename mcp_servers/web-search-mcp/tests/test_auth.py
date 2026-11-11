from __future__ import annotations

import httpx
import pytest

from web_search_mcp.server.app import build_http_app, settings


@pytest.fixture
def _token_env(monkeypatch):
    monkeypatch.setattr(settings, "token", "secret")
    yield


async def test_health_is_exempt(_token_env) -> None:
    transport = httpx.ASGITransport(app=build_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_mcp_requires_token(_token_env) -> None:
    transport = httpx.ASGITransport(app=build_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post("/mcp")
        wrong = await client.post("/mcp", headers={"X-WSM-Token": "nope"})
    assert missing.status_code == 401
    assert wrong.status_code == 401


async def test_empty_token_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "token", "")
    transport = httpx.ASGITransport(app=build_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/mcp")
    assert resp.status_code == 503
