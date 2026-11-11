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

_TRUNCATION_MARKER = "[truncated: max_results reached]"
_MAX_MATCHES = 250
_MAX_LINE_BYTES = 2000

_require_runtime = require_runtime
_ws_kind = ws_kind
_ws_root = ws_root


class GrepTool:
    name = "grep"
    description = (
        "在工作区文件中搜索匹配正则表达式的行。"
        "以 JSON 返回结构化匹配结果：由 {path, line_number, line} 组成的列表。"
        "工具严格遵守工作区边界，并跳过二进制文件。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "正则表达式模式",
            },
            "path": {
                "type": "string",
                "description": (
                    "相对于工作区扫描的目录，默认为 '.'。"
                ),
            },
            "include": {
                "type": "string",
                "description": (
                    "限制扫描文件范围的 glob 模式。"
                    "默认扫描全部文件（'*'）。"
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "返回匹配数的上限，默认 250。",
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "是否区分大小写，默认 true。",
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

    def __init__(
        self,
        resolver: WorkspaceResolver,
        run_timeout_seconds: float = 5.0,
    ) -> None:
        self._resolver = resolver
        self._timeout = run_timeout_seconds

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return error_result(call_id, "grep：缺少必填参数 'pattern'")
        if context.abort.is_set():
            return error_result(call_id, "grep：已中止")

        path_arg = args.get("path", ".")
        if not isinstance(path_arg, str) or not path_arg:
            path_arg = "."
        include = args.get("include", "*")
        if not isinstance(include, str) or not include:
            include = "*"
        max_matches = args.get("max_results", _MAX_MATCHES)
        if not isinstance(max_matches, int) or max_matches <= 0:
            max_matches = _MAX_MATCHES
        case_sensitive = args.get("case_sensitive", True)
        if not isinstance(case_sensitive, bool):
            case_sensitive = True

        try:
            full_path = self._resolver.resolve_within(
                context.workspace_path, path_arg, session_root=session_root(context),
            )
        except WorkspaceBoundaryError as exc:
            return error_result(call_id, f"grep：{exc}")

        if context.abort.is_set():
            return error_result(call_id, "grep：已中止")

        runtime = _require_runtime(context)
        try:
            resp = await runtime.fs_grep(
                pattern=pattern,
                path=str(full_path),
                include=include,
                case_insensitive=not case_sensitive,
                max_matches=max_matches,
                template_id=_ws_kind(context),
                workspace_id=context.workspace_id,
                workspace_root=_ws_root(context),
            )
        except Exception as exc:
            return error_result(call_id, f"grep：运行时执行失败：{exc}")

        matches: list[dict[str, Any]] = resp.get("matches", []) if isinstance(resp, dict) else []
        truncated = bool(resp.get("truncated", False)) if isinstance(resp, dict) else False

        if context.abort.is_set():
            return error_result(call_id, "grep：扫描过程中已中止")

        payload: dict[str, Any] = {"matches": matches, "count": len(matches)}
        if truncated:
            payload["truncated"] = True
            payload["truncated_reason"] = _TRUNCATION_MARKER
        return text_result(call_id, json.dumps(payload, ensure_ascii=False))
