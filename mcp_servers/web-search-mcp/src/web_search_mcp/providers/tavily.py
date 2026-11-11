"""Tavily search provider: REST API calls with key pool cooldown backoff."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..infra.keypool import TavilyKeyPool
from ..infra.security import upgrade_scheme
from . import ProviderOutcome

logger = logging.getLogger(__name__)

_API_URL = "https://api.tavily.com/search"


async def search_tavily(
    *,
    client: httpx.AsyncClient,
    pool: TavilyKeyPool,
    query: str,
    max_results: int,
    allow_url: Any,
    allowed_schemes: list[str],
    timeout_s: float,
) -> ProviderOutcome:
    if pool.empty():
        return ProviderOutcome(status="unavailable")

    await pool.acquire_slot()
    try:
        tries = min(3, max(1, len(pool)))
        for _ in range(tries):
            key = pool.acquire()
            if not key:
                return ProviderOutcome(status="unavailable")
            body = {
                "api_key": key,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
            }
            try:
                resp = await client.post(_API_URL, json=body, timeout=timeout_s)
            except httpx.HTTPError as exc:
                logger.warning("tavily request failed: %s", exc)
                pool.report_failure(key)
                continue
            if resp.status_code != 200:
                pool.report_failure(key, status_code=resp.status_code)
                continue
            try:
                data = resp.json()
            except ValueError:
                pool.report_failure(key)
                continue
            pool.report_success(key)

            answer = data.get("answer") if isinstance(data, dict) else None
            raw_results = data.get("results") if isinstance(data, dict) else None
            out: list[dict[str, Any]] = []
            if isinstance(raw_results, list):
                for item in raw_results:
                    if not isinstance(item, dict):
                        continue
                    title = item.get("title") if isinstance(item.get("title"), str) else ""
                    link = item.get("url") if isinstance(item.get("url"), str) else ""
                    snippet = item.get("content") if isinstance(item.get("content"), str) else ""
                    link = upgrade_scheme(link, allowed_schemes=allowed_schemes)
                    if not title or not link or not allow_url(link):
                        continue
                    entry: dict[str, Any] = {
                    "title": title,
                    "url": link,
                    "content": snippet,
                    "source": "tavily",
                }
                    if "score" in item:
                        entry["score"] = item["score"]
                    out.append(entry)
                    if len(out) >= max_results:
                        break
            return ProviderOutcome(
                results=out,
                status="ok" if out else "no_results",
                answer=answer if isinstance(answer, str) else None,
            )
        return ProviderOutcome(status="error:tavily_unavailable")
    finally:
        pool.release_slot()
