"""Safe single-page fetcher via AnySearch extract API.

When ``anysearch_api_key`` is configured, ``fetch_page`` calls the AnySearch
extract endpoint to retrieve cleaned page content. If the API returns an error
(e.g. 422 for pages it cannot extract), the page is left as snippet-only —
no direct HTTP fallback, since anti-scraping sites reject direct fetches too.
"""

from __future__ import annotations

import logging
from typing import Any

from ..infra.runtime import ServiceState
from ..infra.security import check_url_safety
from ..providers.anysearch import extract_anysearch

logger = logging.getLogger(__name__)

_TRUNCATION_MARKER = "\n\n…[truncated]…"


async def fetch_page(state: ServiceState, url: str) -> dict[str, Any] | None:
    settings = state.settings
    policy_error = check_url_safety(
        url,
        allowed_schemes=settings.scheme_list,
        allowed_domains=settings.domain_list,
    )
    if policy_error:
        logger.info("fetch blocked for %s: %s", url, policy_error)
        return None

    if not settings.anysearch_api_key:
        logger.debug("no anysearch key, skipping extract for %s", url)
        return None

    extracted = await extract_anysearch(
        client=state.client,
        api_key=settings.anysearch_api_key,
        url=url,
        timeout_s=settings.fetch_timeout_s,
    )
    if extracted is None:
        return None

    content = extracted["content"]
    truncated = False
    if len(content) > settings.page_max_chars:
        content = content[: settings.page_max_chars] + _TRUNCATION_MARKER
        truncated = True

    return {
        "url": extracted["url"],
        "title": extracted["title"],
        "content": content,
        "truncated": truncated,
    }
