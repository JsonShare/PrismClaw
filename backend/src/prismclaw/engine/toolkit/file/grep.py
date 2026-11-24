from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from prismclaw.engine.toolkit.builtin.file_helpers import session_root
from prismclaw.engine.toolkit.builtin.shared_tool_helpers import require_runtime, ws_kind, ws_root
from prismclaw.engine.toolkit.file.workspace import WorkspaceBoundaryError, WorkspaceResolver
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.schema.content.content_types import ToolResult

logger = logging.getLogger(__name__)

_TRUNCATION_MARKER = "[truncated: max_results reached]"
_MAX_MATCHES = 250

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
                "description": ("相对于工作区扫描的目录，默认为 '.'。"),
            },
            "include": {
                "type": "string",
                "description": ("限制扫描文件范围的 glob 模式。默认扫描全部文件（'*'）。"),
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
                context.workspace_path,
                path_arg,
                session_root=session_root(context),
            )
        except WorkspaceBoundaryError as exc:
            return error_result(call_id, f"grep：{exc}")

        if context.abort.is_set():
            return error_result(call_id, "grep：已中止")

        if full_path.is_file() and _is_document(full_path):
            return _search_document(
                call_id=call_id,
                full_path=full_path,
                workspace_path=context.workspace_path,
                pattern=pattern,
                case_sensitive=case_sensitive,
                max_matches=max_matches,
                context=context,
            )

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


def _is_document(full_path: Path) -> bool:
    """目标是否为受支持的文档格式（按页模型检索，而非按行）。"""
    from prismclaw.engine.toolkit.office.document_reader import DOC_FORMATS

    return full_path.suffix.lower() in DOC_FORMATS


def _search_document(
    *,
    call_id: str,
    full_path: Path,
    workspace_path: Path,
    pattern: str,
    case_sensitive: bool,
    max_matches: int,
    context: ToolContext,
) -> ToolResult:
    """在文档的已解析页上按块检索，命中项以定位符替代行号。

    只覆盖已解析的页：未解析页不参与，响应中声明覆盖范围，避免模型误以为
    已全文检索（否则检索一次就会退化为解析整本 PDF）。
    """
    from prismclaw.engine.toolkit.office.document_reader import (
        DOC_FORMATS,
        cache_dir,
        content_hash8,
        search_blocks,
    )

    fmt = DOC_FORMATS[full_path.suffix.lower()]
    try:
        raw = full_path.read_bytes()
    except OSError as exc:
        return error_result(call_id, f"grep：{exc}")

    try:
        source_rel = full_path.relative_to(workspace_path).as_posix()
    except ValueError:
        source_rel = full_path.name

    target = cache_dir(workspace_path, raw, source_rel)
    hash8 = content_hash8(raw)
    try:
        matches, parsed_pages, total_pages = search_blocks(
            target,
            pattern,
            case_insensitive=not case_sensitive,
            max_matches=max_matches,
            hash8=hash8,
            raw=raw,
            fmt=fmt,
        )
    except ValueError as exc:
        return error_result(call_id, f"grep：{exc}")

    display = source_rel
    for item in matches:
        item["path"] = display

    has_ocr = any("@vision" in item["line_number"] for item in matches)
    if len(parsed_pages) < total_pages:
        note = (
            f"仅检索了已解析的 {len(parsed_pages)}/{total_pages} 页；"
            f"未解析页请先用 read 读取（read(file_path={display!r}, offset=页码)）后再检索。"
        )
    else:
        note = f"已覆盖全部 {total_pages} 页。"
    if has_ocr:
        note += "命中含 OCR 页，文字可能有识别错误。"

    payload: dict[str, Any] = {
        "matches": matches,
        "count": len(matches),
        "coverage": {
            "parsed_pages": len(parsed_pages),
            "total_pages": total_pages,
            "note": note,
        },
    }
    return text_result(call_id, json.dumps(payload, ensure_ascii=False))
