from __future__ import annotations

from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from prismclaw.app._health import build_health_endpoint


async def test_unready_module_runtime_returns_503() -> None:
    app = FastAPI()
    app.state.module_runtime_coordinator = SimpleNamespace(
        ready=False,
        health={
            "ready": False,
            "applied_version": 1,
            "target_version": 2,
            "error": "candidate failed",
        },
    )
    build_health_endpoint(app)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["module_runtime"]["target_version"] == 2
