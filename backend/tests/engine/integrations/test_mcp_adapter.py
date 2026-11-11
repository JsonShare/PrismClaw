"""MCPToolAdapter structuredContent 映射回归。

web-search-mcp 的 search 工具把搜索证据放在 structuredContent.search_evidence
中,adapter 必须把它桥接成 ToolResult.reference_artifact,前端证据卡片链路
(sidecar → session custom_type)才能继续工作。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import mcp.types as mcp_types

from prismclaw.engine.integrations.mcp.adapter import MCPToolAdapter
from prismclaw.schema.services.mcp_settings import McpServerConfig


def _make_adapter(call_result: mcp_types.CallToolResult) -> MCPToolAdapter:
    tool = mcp_types.Tool(
        name="search",
        description="web search",
        inputSchema={"type": "object", "properties": {}},
    )
    group = SimpleNamespace(call_tool=AsyncMock(return_value=call_result))
    return MCPToolAdapter(
        server_name="web-search-mcp",
        remote_tool=tool,
        server_config=McpServerConfig(name="web-search-mcp", command="echo"),
        group=group,
        sdk_key="search",
    )


async def test_search_evidence_structured_content_maps_to_reference_artifact() -> None:
    evidence = {
        "query": "prismclaw",
        "search_hit_count": 3,
        "usable_source_count": 2,
        "extractable_source_count": 3,
        "provider_statuses": {"duckduckgo": "ok"},
        "has_public_evidence": True,
    }
    payload = {"query": "prismclaw", "result_count": 1, "results": [], "search_evidence": evidence}
    result = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text='{"query":"prismclaw"}')],
        structuredContent=payload,
    )
    adapter = _make_adapter(result)

    tool_result = await adapter.execute({}, SimpleNamespace(tool_call_id="c1"))

    ref = tool_result.reference_artifact
    assert ref is not None
    assert ref.artifact_type == "search_evidence"
    assert ref.search_evidence == evidence
    assert tool_result.ui_artifact is None


async def test_plain_structured_content_yields_no_reference_artifact() -> None:
    result = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text="hello")],
        structuredContent={"unrelated": {"nested": True}},
    )
    adapter = _make_adapter(result)

    tool_result = await adapter.execute({}, SimpleNamespace(tool_call_id="c2"))

    assert tool_result.reference_artifact is None
    assert tool_result.ui_artifact is None
    assert tool_result.content[0].text == "hello"
