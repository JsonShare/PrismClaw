"""Retrieval agent: multi-round search with heuristic reflection.

The whole "agent" is collapsed behind the single ``search`` MCP tool. Given a
natural-language input, it:

1. splits the input into query variants (original + keyword form);
2. fans each variant through Tavily concurrently and merges hits with URL
   canonicalization and per-host caps;
3. reflects on coverage — when too few sources survived round 1, derives
   follow-up queries from the top result titles and runs a second round;
4. concurrently extracts the top pages (SSRF-guarded, AnySearch extract API);
5. synthesizes an extractive markdown report plus the search_evidence contract.

No LLM is involved; every step is deterministic and degrades gracefully —
partial evidence is reported (never dropped) when the total time budget is hit,
and a total provider failure yields an empty report with diagnostics.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from ..fetch.fetcher import fetch_page
from ..infra.runtime import ServiceState
from ..infra.security import check_url_safety
from ..infra.urlutil import canonicalize_url
from ..providers.anysearch import search_anysearch
from ..providers.tavily import search_tavily
from . import report as report_mod
from .evidence import build_search_evidence

logger = logging.getLogger(__name__)

_SNIPPET_ONLY = "snippet_only"
_CONTENT_OK = "content"

# Interrogative / filler words dropped when deriving the keyword form of a
# query. Mixed CJK + Latin because the agent is language-agnostic.
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "of",
        "for",
        "and",
        "or",
        "to",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "do",
        "does",
        "did",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "that",
        "this",
        "these",
        "those",
        "with",
        "about",
        "into",
        "from",
        "的",
        "了",
        "是",
        "在",
        "和",
        "与",
        "及",
        "对",
        "把",
        "被",
        "让",
        "向",
        "从",
        "关于",
        "对于",
        "为什么",
        "什么",
        "怎么",
        "怎样",
        "如何",
        "哪些",
        "哪个",
        "谁",
        "何时",
    ]
)

_TOKEN_SPLIT_RE = re.compile(r"[^\w\u4e00-\u9fff]+")
# CJK chunks 2-12 chars are kept whole; Latin/digit tokens >= 2 chars survive.
_SALIENT_CHUNK_RE = re.compile(r"[\u4e00-\u9fff]{2,12}|[A-Za-z0-9][A-Za-z0-9+\-.#]{1,}")


def _host_of(raw_url: str) -> str:
    try:
        return (urlparse(raw_url).hostname or "").lower()
    except ValueError:
        return ""


def split_query_variants(query: str, *, max_variants: int) -> list[str]:
    """[original, keyword-form] — deterministic, order-preserving, deduplicated."""
    variants = [query.strip()]
    tokens = [t for t in _TOKEN_SPLIT_RE.split(query) if t and t.lower() not in _STOPWORDS]
    keyword_form = " ".join(tokens).strip()
    if keyword_form and keyword_form != variants[0]:
        variants.append(keyword_form)
    return variants[: 1 + max(0, max_variants)]


def follow_up_queries(
    query: str, results: list[dict[str, Any]], *, max_followups: int
) -> list[str]:
    """Derive follow-up queries from salient chunks of top result titles."""
    followups: list[str] = []
    for item in results[:3]:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        for chunk in _SALIENT_CHUNK_RE.findall(title):
            if chunk.lower() in _STOPWORDS or len(chunk) < 2:
                continue
            candidate = f"{query} {chunk}".strip()
            if candidate != query and candidate not in followups:
                followups.append(candidate)
                break  # one follow-up per title
        if len(followups) >= max_followups:
            break
    return followups


def _merge_results(
    *,
    merged: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    seen: set[str],
    host_counts: dict[str, int],
    max_results: int,
    max_per_host: int,
) -> None:
    for item in incoming:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        canonical = item.get("canonical_url") or canonicalize_url(url)
        if canonical in seen:
            continue
        host = _host_of(canonical)
        if host:
            if host_counts.get(host, 0) >= max_per_host:
                continue
            host_counts[host] = host_counts.get(host, 0) + 1
        item["canonical_url"] = canonical
        seen.add(canonical)
        merged.append(item)
        if len(merged) >= max_results:
            return


async def _run_search(
    state: ServiceState,
    *,
    query: str,
    max_results: int,
    language: str | None,
    allow_url: Callable[[str], bool],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    settings = state.settings
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    host_counts: dict[str, int] = {}
    diagnostics: dict[str, str] = {}
    merge = lambda incoming: _merge_results(  # noqa: E731
        merged=merged,
        incoming=incoming,
        seen=seen,
        host_counts=host_counts,
        max_results=max_results,
        max_per_host=settings.max_per_host,
    )

    # 1) Concurrent provider calls (Tavily + AnySearch in parallel)
    provider_tasks: list[tuple[str, Any]] = []
    if settings.tavily_keys:
        provider_tasks.append(("tavily", search_tavily(
            client=state.client,
            pool=state.tavily_pool,
            query=query,
            max_results=max_results,
            allow_url=allow_url,
            allowed_schemes=settings.scheme_list,
            timeout_s=settings.search_timeout_s,
        )))
    if settings.anysearch_api_key:
        provider_tasks.append(("anysearch", search_anysearch(
            client=state.client,
            api_key=settings.anysearch_api_key,
            query=query,
            max_results=max_results,
            language=language,
            allow_url=allow_url,
            allowed_schemes=settings.scheme_list,
            timeout_s=settings.search_timeout_s,
        )))

    if provider_tasks:
        outcomes = await asyncio.gather(*(task for _, task in provider_tasks))
        for (name, _), outcome in zip(provider_tasks, outcomes, strict=True):
            diagnostics[name] = outcome.status
            merge(outcome.results)

    for item in merged:
        item.setdefault("source", "unknown")
    return merged, diagnostics


class _MergeState:
    """Global dedup/host-cap state shared across agent rounds."""

    def __init__(self) -> None:
        self.merged: list[dict[str, Any]] = []
        self.seen: set[str] = set()
        self.host_counts: dict[str, int] = {}

    def merge(self, incoming: list[dict[str, Any]], *, max_results: int, max_per_host: int) -> None:
        _merge_results(
            merged=self.merged,
            incoming=incoming,
            seen=self.seen,
            host_counts=self.host_counts,
            max_results=max_results,
            max_per_host=max_per_host,
        )


async def _search_round(
    state: ServiceState,
    *,
    queries: list[str],
    max_results: int,
    language: str | None,
    allow_url: Callable[[str], bool],
    merge_state: _MergeState,
) -> tuple[dict[str, str], bool]:
    """Run every query variant of one round concurrently; merge into shared state.

    Returns (per-provider diagnostics, completed) where completed is False when
    the round was cut short by its time slice.
    """
    settings = state.settings
    diagnostics: dict[str, str] = {}

    async def _one(query: str) -> tuple[list[dict[str, Any]], dict[str, str]] | None:
        try:
            return await _run_search(
                state,
                query=query,
                max_results=max_results,
                language=language,
                allow_url=allow_url,
            )
        except TimeoutError:
            return None

    completed = True
    try:
        outcomes = await asyncio.gather(*(_one(q) for q in queries))
    except TimeoutError:
        outcomes = []
        completed = False

    for outcome in outcomes:
        if outcome is None:
            completed = False
            continue
        items, diag = outcome
        merge_state.merge(items, max_results=max_results, max_per_host=settings.max_per_host)
        for key, value in diag.items():
            diagnostics[key] = value
    return diagnostics, completed


async def _fetch_top(state: ServiceState, merged: list[dict[str, Any]]) -> None:
    targets = merged[: state.settings.max_fetch_pages]
    if not targets:
        return

    # Skip extract/fetch when search already returned enough content (e.g. Tavily
    # returns ~1000+ char snippets that are sufficient for the report).
    _SUFFICIENT_CONTENT_THRESHOLD = 800

    async def _fill(item: dict[str, Any]) -> None:
        if len(item.get("content") or "") >= _SUFFICIENT_CONTENT_THRESHOLD:
            item["content_status"] = _CONTENT_OK
            return
        page = await fetch_page(state, item["url"])
        if page is None:
            item["content_status"] = _SNIPPET_ONLY
            return
        item["content"] = page["content"]
        if page["title"]:
            item["title"] = page["title"]
        item["url"] = page["url"]
        item["truncated"] = page["truncated"]
        item["content_status"] = _CONTENT_OK

    await asyncio.gather(*(_fill(item) for item in targets), return_exceptions=True)


def _public_payload(
    query: str,
    merged: list[dict[str, Any]],
    diagnostics: dict[str, str],
    *,
    truncated: bool,
    rounds: list[dict[str, Any]],
    settings: Any,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in merged:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": item.get("source", "unknown"),
                "content": item.get("content", ""),
                "content_status": item.get("content_status", _SNIPPET_ONLY),
            }
        )
    evidence = build_search_evidence(query=query, results=results, diagnostics=diagnostics)
    markdown = report_mod.build_markdown_report(
        query=query,
        results=results,
        rounds=rounds,
        truncated=truncated,
        excerpt_chars=settings.report_excerpt_chars,
    )
    return {
        "query": query,
        "result_count": len(results),
        "results": results,
        "truncated": truncated,
        "diagnostics": diagnostics,
        "search_evidence": evidence,
        "report_markdown": markdown,
        "agent_trace": {"rounds": rounds},
    }


async def run(
    state: ServiceState,
    *,
    query: str,
    num_results: int,
    language: str | None,
    do_fetch: bool,
) -> dict[str, Any]:
    settings = state.settings
    capped = max(1, min(num_results, settings.max_num_results))
    cache_key = f"{query.strip()}|{capped}|{language or ''}|fetch={int(do_fetch)}"
    cached = state.cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    def allow_url(raw: str) -> bool:
        return (
            check_url_safety(
                raw,
                allowed_schemes=settings.scheme_list,
                allowed_domains=settings.domain_list,
            )
            is None
        )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.total_timeout_s
    merge_state = _MergeState()
    diagnostics: dict[str, str] = {}
    rounds: list[dict[str, Any]] = []
    truncated = False

    round_queries = split_query_variants(query, max_variants=settings.max_query_variants)
    for round_no in range(1, max(1, settings.max_agent_rounds) + 1):
        remaining = deadline - loop.time()
        if remaining <= 0.5:
            break
        round_diag: dict[str, str] = {}
        completed = True
        try:
            async with asyncio.timeout(remaining):
                round_diag, completed = await _search_round(
                    state,
                    queries=round_queries,
                    max_results=capped,
                    language=language,
                    allow_url=allow_url,
                    merge_state=merge_state,
                )
        except TimeoutError:
            completed = False
        for key, value in round_diag.items():
            diagnostics[key] = value
        rounds.append(
            {"round": round_no, "queries": list(round_queries), "statuses": dict(round_diag)}
        )
        if not completed:
            break

        # Reflection: enough hits → stop; thin round 1 → derive follow-ups.
        if len(merge_state.merged) >= capped:
            truncated = True
            break
        if (
            round_no < settings.max_agent_rounds
            and len(merge_state.merged) < settings.min_sources_before_followup
        ):
            followups = follow_up_queries(
                query,
                merge_state.merged,
                max_followups=settings.max_query_variants,
            )
            if followups:
                round_queries = followups
                continue
        break

    merged = merge_state.merged
    try:
        if merged and do_fetch:
            async with asyncio.timeout(max(0.5, deadline - loop.time())):
                await _fetch_top(state, merged)
        for item in merged:
            item.setdefault("content_status", _SNIPPET_ONLY)
        payload = _public_payload(
            query,
            merged,
            diagnostics,
            truncated=truncated,
            rounds=rounds,
            settings=settings,
        )
    except TimeoutError:
        logger.warning("retrieval agent timed out during fetch for query=%r", query)
        for item in merged:
            item.setdefault("content_status", _SNIPPET_ONLY)
        payload = _public_payload(
            query,
            merged,
            diagnostics,
            truncated=truncated,
            rounds=rounds,
            settings=settings,
        )

    state.cache.set(cache_key, payload)
    return payload
