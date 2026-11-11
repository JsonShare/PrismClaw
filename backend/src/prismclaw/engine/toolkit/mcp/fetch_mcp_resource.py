from __future__ import annotations

import json
import logging
from typing import Any

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.schema.content.content_types import ToolResult

logger = logging.getLogger(__name__)


def _safe_get_status(manager: Any, name: str) -> Any | None:
    getter = getattr(manager, "get_status", None)
    if not callable(getter):
        return None
    status = getter(name)
    if getattr(status, "name", None) != name:
        return None
    if getattr(status, "status", None) not in {"connected", "failed", "disabled", "pending"}:
        return None
    return status


class FetchMcpResourceTool:
    name = "fetch_mcp_resource"
    description = (
        "从已连接的 MCP 服务读取指定资源。"
        "用于获取该服务在 `resources` 接口下公布的可由 URI 定位的产物（文件、PR、查询等）。"
        "该服务必须处于已连接状态；可用 `/mcp list` 确认。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "已配置的 MCP 服务名（可通过 /mcp list 查看可用服务）",
            },
            "uri": {
                "type": "string",
                "description": "服务所提供的 MCP 资源 URI",
            },
        },
        "required": ["server", "uri"],
        "additionalProperties": False,
    }
    side_effect = False
    tool_class = "read"
    effect_class = "pure_read"

    def __init__(self, mcp_manager: Any) -> None:
        self._mcp_manager = mcp_manager

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        server = args.get("server")
        uri = args.get("uri")
        if not isinstance(server, str) or not server:
            return error_result(call_id, "fetch_mcp_resource：缺少必填参数 'server'")
        if not isinstance(uri, str) or not uri:
            return error_result(call_id, "fetch_mcp_resource：缺少必填参数 'uri'")
        if context.abort.is_set():
            return error_result(call_id, "fetch_mcp_resource：已中止")

        configured = {s.name for s in self._mcp_manager.list_servers()}
        if server not in configured:
            return error_result(
                call_id,
                f"fetch_mcp_resource：服务 {server!r} 未配置"
                f"（已知：{sorted(configured)!r}）",
            )

        status = _safe_get_status(self._mcp_manager, server)
        session = self._mcp_manager.get_session(server)
        if session is None:
            if status is not None and status.status == "disabled":
                return error_result(
                    call_id,
                    f"fetch_mcp_resource：服务 {server!r} 已禁用或仅为声明式配置，"
                    f"没有可用的 MCP 会话"
                    + (f" ({status.reason})" if status.reason else ""),
                )
            if status is not None and status.status == "pending":
                return error_result(
                    call_id,
                    f"fetch_mcp_resource：服务 {server!r} 仍在连接中"
                    f"（status=pending；可用 /mcp list 等待最终状态）",
                )
            reason = f"; reason={status.reason}" if status is not None and status.reason else ""
            return error_result(
                call_id,
                f"fetch_mcp_resource：服务 {server!r} 未连接"
                f"（可用 /mcp list 查看状态，或用 /mcp restart {server} 重新连接{reason}）",
            )

        try:
            response = await session.read_resource(uri)
        except Exception as exc:
            logger.warning(
                "fetch_mcp_resource: server=%s uri=%s error=%s",
                server,
                uri,
                exc,
            )
            return error_result(
                call_id,
                f"fetch_mcp_resource：服务 {server!r} 读取 {uri!r} 时出错："
                f"{type(exc).__name__}: {exc}",
            )

        contents = getattr(response, "contents", None) or []
        if not contents:
            return text_result(call_id, "")

        text_parts: list[str] = []
        for item in contents:
            text = getattr(item, "text", None)
            if text:
                text_parts.append(text)
                continue
            blob = getattr(item, "blob", None)
            if blob:
                text_parts.append(
                    f"[二进制数据块，mimeType={getattr(item, 'mimeType', 'unknown')!r}，"
                    f"长度={len(blob)}]"
                )
                continue
            text_parts.append(json.dumps(_safe_dump(item), ensure_ascii=False))

        return text_result(call_id, "\n\n".join(text_parts))


def _safe_dump(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        try:
            return item.model_dump(mode="json")
        except Exception:
            pass
    return {"repr": repr(item)}
