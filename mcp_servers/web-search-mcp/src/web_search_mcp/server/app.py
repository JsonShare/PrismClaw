"""FastMCP server exposing the single ``search`` tool and the ASGI HTTP app."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from starlette.responses import JSONResponse

from ..agent import core as agent
from ..agent import report as report_mod
from ..config import Settings
from ..infra.runtime import close_state, init_state
from .auth import TokenAuthMiddleware

logger = logging.getLogger(__name__)

settings = Settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

# PrismClaw 客户端注入：server 进程内文件日志（logs/mcp_server_<name>/）
_infra_path = os.environ.get("PRISMCLAW_INFRA_PATH")
if _infra_path and _infra_path not in sys.path:
    sys.path.insert(0, _infra_path)
try:
    from mcp_logkit import install_from_env

    install_from_env()
except ImportError:
    pass


@asynccontextmanager
async def lifespan(_app: Any):
    init_state(settings)
    try:
        yield
    finally:
        await close_state()


mcp = FastMCP(
    "web-search-mcp",
    instructions=(
        "通用网络检索代理：输入信息需求，自动执行多轮网络搜索（含启发式追问），"
        "提取最相关页面正文，返回带引用来源的 markdown 报告。"
        "无需后续 search/fetch/extract 调用。"
        "用于企业资料、公司信息、新闻、人物、产品、公开数据等通用网络检索；"
        "学术论文/医学文献请使用专用学术工具。"
    ),
    lifespan=lifespan,
)


@mcp.tool(
    name="search",
    title="Web retrieval agent",
    description=(
        "网络检索代理 — 通用网络检索的默认工具。"
        "用于任何需要公开/时效性/事实性网络证据的问题："
        "企业资料、公司信息、新闻资讯、人物背景、产品动态、技术趋势、公开数据等。"
        "运行多轮网络搜索（首轮结果不足时自动追问细化），提取最相关页面内容，"
        "返回带引用来源的 markdown 报告。设 fetch=false 仅返回摘要片段。"
        " 通用网络检索一律用本工具；学术论文/指南/文献等请用专用学术工具。"
    ),
    annotations=ToolAnnotations(
        title="Web retrieval agent",
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=True,
    ),
    structured_output=False,
)
async def search_tool(
    query: str,
    num_results: int = 6,
    language: str | None = None,
    fetch: bool = True,
) -> CallToolResult:
    """Run the retrieval agent and return a markdown report.

    Args:
        query: Natural-language information need.
        num_results: Maximum number of sources (default 6, hard cap 20).
        language: Optional BCP-47 language hint, e.g. "zh-CN" or "en".
        fetch: When true (default) top pages are fetched and extracted inline.
    """
    state = init_state(settings)
    payload = await agent.run(
        state,
        query=query,
        num_results=num_results,
        language=language,
        do_fetch=fetch,
    )
    markdown = payload["report_markdown"] + report_mod.machine_footer(
        query=payload["query"],
        results=payload["results"],
        result_count=payload["result_count"],
        truncated=payload["truncated"],
    )
    # Custom dual-channel output: TextContent = report, structuredContent =
    # full contract payload (returned verbatim by the low-level server).
    return CallToolResult(
        content=[TextContent(type="text", text=markdown.rstrip())],
        structuredContent=payload,
        isError=False,
    )


@mcp.custom_route("/health", methods=["GET"])
async def _health(_request: Any) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "web-search-mcp"})


def build_http_app():
    """ASGI app: FastMCP's /mcp endpoint plus an unauthenticated /health."""
    app = mcp.streamable_http_app()
    app.add_middleware(TokenAuthMiddleware, token=settings.token)
    return app
