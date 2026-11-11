from __future__ import annotations

import json
import logging
from typing import Any

from prismclaw.engine.toolkit.builtin.file_helpers import session_root
from prismclaw.engine.toolkit.builtin.shared_tool_helpers import require_runtime, ws_kind, ws_root
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.engine.toolkit.file.workspace import WorkspaceBoundaryError, WorkspaceResolver
from prismclaw.schema.content.content_types import ToolResult

logger = logging.getLogger(__name__)


# ─── 辅助函数（与 builtin.py 保持一致） ─────────────────────────────

_require_runtime = require_runtime
_ws_root = ws_root
_ws_kind = ws_kind


# ─── GlobTool ────────────────────────────────────────────────────────


_MAX_RESULTS = 100


class GlobTool:
    name = "glob"
    description = (
        "按 glob 模式列出工作区内的文件，并按修改时间倒序排列（最新的在前）。"
        "工具严格遵守工作区边界；解析后指向工作区外部的符号链接会被静默排除。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "glob 模式（例如 '**/*.py'）",
            },
            "path": {
                "type": "string",
                "description": (
                    "相对于工作区扫描的目录，默认为 '.'。"
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "返回路径数的上限，默认 100。",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }
    side_effect = False
    tool_class = "read"
    effect_class = "pure_read"
    approval_eligible = False
    is_concurrency_safe = True

    def __init__(self, resolver: WorkspaceResolver) -> None:
        self._resolver = resolver

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return error_result(call_id, "glob：缺少必填参数 'pattern'")
        if context.abort.is_set():
            return error_result(call_id, "glob：已中止")

        path_arg = args.get("path", ".")
        if not isinstance(path_arg, str) or not path_arg:
            path_arg = "."
        max_results = args.get("max_results", _MAX_RESULTS)
        if not isinstance(max_results, int) or max_results <= 0:
            max_results = _MAX_RESULTS

        try:
            full_path = self._resolver.resolve_within(
                context.workspace_path, path_arg, session_root=session_root(context),
            )
        except WorkspaceBoundaryError as exc:
            return error_result(call_id, f"glob：{exc}")

        runtime = _require_runtime(context)

        try:
            resp = await runtime.fs_glob(
                pattern=pattern,
                path=str(full_path),
                max_results=max_results,
                template_id=_ws_kind(context),
                workspace_id=context.workspace_id,
                workspace_root=_ws_root(context),
            )
        except Exception as exc:
            return error_result(call_id, f"glob：运行时执行失败：{exc}")

        entries: list[dict[str, Any]] = resp.get("entries", []) if isinstance(resp, dict) else []
        truncated = bool(resp.get("truncated", False)) if isinstance(resp, dict) else False

        if context.abort.is_set():
            return error_result(call_id, "glob：扫描过程中已中止")

        paths = [entry.get("path", "") for entry in entries]
        payload: dict[str, Any] = {"paths": paths, "count": len(paths)}
        if truncated:
            payload["truncated"] = True
            payload["truncated_reason"] = "max_results reached"
        return text_result(call_id, json.dumps(payload, ensure_ascii=False))
