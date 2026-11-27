from __future__ import annotations

import json

import pytest

from web_search_mcp.agent import core as agent
from web_search_mcp.agent.report import MACHINE_FOOTER_PREFIX
from web_search_mcp.providers import ProviderOutcome
from web_search_mcp.server.app import mcp, search_tool


async def _tavily(**_kwargs):
    return ProviderOutcome(
        results=[
            {
                "title": "Doc",
                "url": "https://docs.example.com/api",
                "content": "snippet",
                "source": "tavily",
            }
        ],
        status="ok",
    )


async def _fetch(_state, url):
    return {"url": url, "title": "Doc", "content": "cleaned body", "truncated": False}


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(agent, "search_tavily", _tavily)
    monkeypatch.setattr(agent, "fetch_page", _fetch)
    yield


async def test_tool_returns_markdown_content_and_structured_contract() -> None:
    result = await search_tool(query="api docs", num_results=3, language="en", fetch=True)
    assert result.is_error is False

    # TextContent: markdown report + machine footer
    assert len(result.content) == 1
    text = result.content[0].text
    assert text.startswith("# api docs — Web Retrieval Report")
    assert MACHINE_FOOTER_PREFIX in text
    footer_blob = text.rsplit(MACHINE_FOOTER_PREFIX, 1)[1].rsplit("-->", 1)[0]
    machine = json.loads(footer_blob)
    assert machine["query"] == "api docs"
    assert machine["result_count"] == 1
    assert machine["results"][0]["url"] == "https://docs.example.com/api"

    # structuredContent: full contract payload (engine adapter reads search_evidence)
    payload = result.structured_content
    assert set(payload) == {
        "query",
        "result_count",
        "results",
        "truncated",
        "diagnostics",
        "search_evidence",
        "report_markdown",
        "agent_trace",
    }
    assert set(payload["results"][0]) == {"title", "url", "source", "content", "content_status"}
    assert payload["results"][0]["content"] == "cleaned body"
    assert payload["results"][0]["content_status"] == "content"
    assert payload["search_evidence"]["query"] == "api docs"
    assert payload["report_markdown"].startswith("# api docs")


async def test_tool_registered_with_readonly_annotation() -> None:
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {"search", "fetch"}

    tool = by_name["search"]
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    props = tool.input_schema["properties"]
    assert set(props) == {"query", "num_results", "language", "fetch"}
    assert tool.input_schema["required"] == ["query"]

    fetch_tool = by_name["fetch"]
    assert fetch_tool.annotations is not None
    assert fetch_tool.annotations.read_only_hint is True
    assert fetch_tool.annotations.destructive_hint is False
    assert set(fetch_tool.input_schema["properties"]) == {"url"}
    assert fetch_tool.input_schema["required"] == ["url"]
