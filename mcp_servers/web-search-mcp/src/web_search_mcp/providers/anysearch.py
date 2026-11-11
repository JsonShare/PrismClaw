"""AnySearch provider: search + extract via REST API.

API docs:
  - POST https://api.anysearch.com/v1/search
  - POST https://api.anysearch.com/v1/extract

Rate limits (free tier): 1,000 requests/day, 20 QPS per key.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..infra.security import upgrade_scheme
from . import ProviderOutcome

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.anysearch.com/v1/search"
_EXTRACT_URL = "https://api.anysearch.com/v1/extract"


async def search_anysearch(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    query: str,
    max_results: int,
    language: str | None = None,
    allow_url: Any = None,
    allowed_schemes: list[str] | None = None,
    timeout_s: float = 12.0,
) -> ProviderOutcome:
    """Call AnySearch /v1/search and map to ProviderOutcome."""
    if not api_key:
        return ProviderOutcome(status="unavailable")

    body: dict[str, Any] = {
        "query": query,
        "max_results": min(max(1, max_results), 10),
        "format": "markdown",
    }
    if language:
        body["language"] = language

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = await client.post(_SEARCH_URL, json=body, headers=headers, timeout=timeout_s)
    except httpx.HTTPError as exc:
        logger.warning("anysearch request failed: %s", exc)
        return ProviderOutcome(status="error:anysearch_network")

    if resp.status_code == 429:
        return ProviderOutcome(status="rate_limited")
    if resp.status_code in (401, 403):
        logger.warning("anysearch auth error: HTTP %s", resp.status_code)
        return ProviderOutcome(status="error:anysearch_auth")
    if resp.status_code == 402:
        logger.warning("anysearch quota exhausted")
        return ProviderOutcome(status="quota_exhausted")
    if resp.status_code != 200:
        logger.warning("anysearch HTTP %s", resp.status_code)
        return ProviderOutcome(status=f"error:anysearch_http_{resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        return ProviderOutcome(status="error:anysearch_parse")

    if data.get("code") != 0:
        logger.warning("anysearch business error: %s", data.get("message", ""))
        return ProviderOutcome(status="error:anysearch_business")

    results_raw = (data.get("data") or {}).get("results") or []
    schemes = allowed_schemes or ["https"]
    out: list[dict[str, Any]] = []
    for item in results_raw:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        link = item.get("url") or ""
        content = item.get("content") or item.get("snippet") or ""
        if not link:
            continue
        link = upgrade_scheme(link, allowed_schemes=schemes)
        if allow_url and not allow_url(link):
            continue
        out.append({
            "title": title,
            "url": link,
            "content": content,
            "source": "anysearch",
        })
        if len(out) >= max_results:
            break

    return ProviderOutcome(
        results=out,
        status="ok" if out else "no_results",
    )


async def extract_anysearch(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    url: str,
    timeout_s: float = 15.0,
) -> dict[str, Any] | None:
    """Call AnySearch /v1/extract and return a fetch_page-compatible dict.

    Returns None on any error; the caller leaves the page as snippet-only.
    """
    if not api_key:
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {"url": url}

    try:
        resp = await client.post(_EXTRACT_URL, json=body, headers=headers, timeout=timeout_s)
    except httpx.HTTPError as exc:
        logger.debug("anysearch extract failed: %s", exc)
        return None

    if resp.status_code != 200:
        logger.debug("anysearch extract HTTP %s for %s", resp.status_code, url)
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    if data.get("code") != 0:
        return None

    payload = data.get("data") or {}
    content = (payload.get("content") or "").strip()
    if not content:
        return None

    return {
        "url": payload.get("url") or url,
        "title": (payload.get("title") or "").strip(),
        "content": content,
        "truncated": False,
    }
