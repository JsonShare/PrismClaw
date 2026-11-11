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
    assert result.isError is False

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
    payload = result.structuredContent
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
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "search"
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    props = tool.inputSchema["properties"]
    assert set(props) == {"query", "num_results", "language", "fetch"}
    assert tool.inputSchema["required"] == ["query"]
