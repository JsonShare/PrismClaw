from __future__ import annotations

import pytest

from web_search_mcp.agent import core as agent
from web_search_mcp.providers import ProviderOutcome


def _hit(title: str, url: str, content: str = "snip") -> dict:
    return {"title": title, "url": url, "content": content, "source": "tavily"}


async def _fake_fetch(_state, url):
    return {"url": url, "title": "Fetched", "content": "full body text", "truncated": False}


@pytest.fixture(autouse=True)
def _patch_providers(monkeypatch):
    async def _tavily(**_kwargs):
        return ProviderOutcome(
            results=[
                _hit("A", "https://a.example.com/1"),
                _hit("A2", "https://a.example.com/2"),
                _hit("B", "https://b.example.org/x"),
            ],
            status="ok",
        )

    monkeypatch.setattr(agent, "search_tavily", _tavily)
    monkeypatch.setattr(agent, "fetch_page", _fake_fetch)
    yield


async def test_agent_end_to_end_markdown_report(state) -> None:
    payload = await agent.run(state, query="你好", num_results=6, language=None, do_fetch=True)
    assert payload["query"] == "你好"
    assert payload["result_count"] == 3
    titles = [r["title"] for r in payload["results"]]
    assert titles == ["Fetched", "Fetched", "Fetched"]
    assert all(r["content_status"] == "content" for r in payload["results"])

    md = payload["report_markdown"]
    assert md.startswith("# 你好 — 网络检索报告")
    assert "## 来源" in md
    assert "https://a.example.com/1" in md
    assert "全文抓取" in md

    ev = payload["search_evidence"]
    assert ev["search_hit_count"] == 3
    assert ev["usable_source_count"] == 3
    assert ev["has_public_evidence"] is True
    assert payload["diagnostics"]["tavily"] == "ok"
    assert payload["agent_trace"]["rounds"][0]["queries"][0] == "你好"


async def test_english_query_produces_english_report(state) -> None:
    payload = await agent.run(
        state, query="asyncio tutorial", num_results=3, language="en", do_fetch=False
    )
    md = payload["report_markdown"]
    assert md.startswith("# asyncio tutorial — Web Retrieval Report")
    assert "## Sources" in md


async def test_reflection_round_when_first_round_thin(state, monkeypatch) -> None:
    calls: list[str] = []

    async def _thin_tavily(*, query, **_kwargs):
        calls.append(query)
        # round 1: original only ("python asyncio" derives no keyword variant)
        if len(calls) <= 1:
            return ProviderOutcome(
                results=[_hit("Thin", "https://thin.example.com/1")], status="ok"
            )
        return ProviderOutcome(
            results=[
                _hit("Follow 1", "https://f.example.com/1"),
                _hit("Follow 2", "https://f.example.com/2"),
            ],
            status="ok",
        )

    monkeypatch.setattr(agent, "search_tavily", _thin_tavily)
    state.settings.max_agent_rounds = 2

    payload = await agent.run(
        state, query="python asyncio", num_results=6, language=None, do_fetch=False
    )
    rounds = payload["agent_trace"]["rounds"]
    assert len(rounds) == 2
    # round 2 queries are derived from the round-1 hit title "Thin"
    assert rounds[1]["queries"] and rounds[1]["queries"] != rounds[0]["queries"]
    titles = [r["title"] for r in payload["results"]]
    assert "Thin" in titles and "Follow 1" in titles


async def test_per_host_cap_and_canonical_dedup(state, monkeypatch) -> None:
    async def _tavily(**_kwargs):
        return ProviderOutcome(
            results=[
                _hit("1", "https://h.example.com/a"),
                _hit("2", "https://h.example.com/b?utm_source=x"),
                _hit("3", "https://h.example.com/c"),
            ],
            status="ok",
        )

    monkeypatch.setattr(agent, "search_tavily", _tavily)
    payload = await agent.run(state, query="q", num_results=6, language=None, do_fetch=False)
    # host cap = 2, and canonical(a) == canonical(b+utm) → only 2 hits survive
    assert payload["result_count"] == 2
    assert all(r["content_status"] == "snippet_only" for r in payload["results"])


async def test_num_results_hard_cap(state) -> None:
    payload = await agent.run(state, query="q", num_results=999, language=None, do_fetch=False)
    assert payload["result_count"] <= state.settings.max_num_results


async def test_cache_hit_skips_providers(state, monkeypatch) -> None:
    from web_search_mcp.infra.cache import TTLCache

    state.settings.cache_ttl_seconds = 60
    state.cache = TTLCache(ttl_seconds=60, max_size=256)
    calls = {"n": 0}

    async def _counting_tavily(**_kwargs):
        calls["n"] += 1
        return ProviderOutcome(results=[_hit("x", "https://x.example.com/")], status="ok")

    monkeypatch.setattr(agent, "search_tavily", _counting_tavily)
    # "cached" derives no keyword variant → exactly one provider call per run
    first = await agent.run(state, query="cached", num_results=6, language=None, do_fetch=False)
    second = await agent.run(state, query="cached", num_results=6, language=None, do_fetch=False)
    assert calls["n"] == 1
    assert second == first


async def test_all_providers_empty_returns_degraded_report(state, monkeypatch) -> None:
    async def _empty_tavily(**_kwargs):
        return ProviderOutcome(results=[], status="no_results_or_blocked")

    monkeypatch.setattr(agent, "search_tavily", _empty_tavily)
    payload = await agent.run(state, query="中文检索", num_results=6, language=None, do_fetch=False)
    assert payload["result_count"] == 0
    assert payload["results"] == []
    assert payload["search_evidence"]["has_public_evidence"] is False
    assert "未检索到可用来源" in payload["report_markdown"]
