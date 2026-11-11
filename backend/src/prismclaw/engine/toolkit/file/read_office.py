"""Document reading branch — shared by the read tool for document formats.

Supports: .docx, .xlsx, .pptx, .pdf, .md/.markdown/.rst.
Returns text content in document order, with embedded images extracted to disk
and referenced inline as ``[图片: path — 使用 read 工具识别图片内容]``.
For .xlsx/.pptx, structural anchors (s#S!r#Rc#C, slide#N/tf#M) are preserved.

Formerly the standalone ``read_office`` tool (M1 merge): the parsing engines
below are unchanged; the entry point is now a module-level coroutine invoked
from ReadTool with offset/limit pagination instead of page numbers.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from prismclaw.engine.toolkit.office.document_image_extract import (
    extract_document_blocks,
)
from prismclaw.engine.review.changes.office_engines import (
    read_office_text,
)
from prismclaw.engine.toolkit.builtin.file_helpers import session_root
from prismclaw.engine.toolkit.builtin.shared_tool_helpers import require_runtime, ws_kind, ws_root
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.engine.toolkit.file.ocr_cache import analysis_md_path, read_ocr_cache
from prismclaw.engine.toolkit.file.workspace import WorkspaceBoundaryError, WorkspaceResolver
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.agent.data_types import ImageBlock, TextBlock

# 支持的文档格式：扩展名 → extract_document_blocks 使用的 fmt 键
_DOC_FMT_BY_SUFFIX = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "markdown",
}

# 结构化 Office 格式（走 read_office_text 保留编辑锚点）
_STRUCT_OFFICE_SUFFIXES = {".xlsx", ".pptx"}

_ALL_SUPPORTED_SUFFIXES = set(_DOC_FMT_BY_SUFFIX) | _STRUCT_OFFICE_SUFFIXES

# 分页：每页行数，与 read 工具的 DEFAULT_PAGE_LINES 对齐
_PAGE_LINES = 2000


def _fmt_for_path(path: str) -> str | None:
    """根据路径后缀返回文档格式键，不支持返回 None。"""
    ext = Path(path).suffix.lower()
    if ext in _DOC_FMT_BY_SUFFIX:
        return _DOC_FMT_BY_SUFFIX[ext]
    if ext in _STRUCT_OFFICE_SUFFIXES:
        return ext.lstrip(".")  # "xlsx" / "pptx"
    return None


def _blocks_to_text(blocks: list) -> str:
    """按文档顺序内联输出：文本直接拼接，图片插入 read 引用占位。"""
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, TextBlock):
            if b.text:
                parts.append(b.text)
        elif isinstance(b, ImageBlock) and b.source_path:
            parts.append(
                f"[图片: {b.source_path} — 使用 read 工具识别图片内容]"
            )
    return "".join(parts)


def _write_analysis_md(blocks: list, path: Path, workspace_path: str | None = None):
    """将 blocks 按序写入 analysis.md，ImageBlock 查 OCR 缓存回填。"""
    parts = ["# 文档解析\n\n"]
    for b in blocks:
        if isinstance(b, TextBlock) and b.text:
            parts.append(b.text)
            parts.append("\n\n---\n\n")
        elif isinstance(b, ImageBlock) and b.source_path:
            # read_ocr_cache 需要 stat 文件，必须传绝对路径
            abs_img_path = str(Path(workspace_path) / b.source_path) if workspace_path else b.source_path
            ocr_text = read_ocr_cache(workspace_path, abs_img_path) if workspace_path else ""
            parts.append(f"## [图片: {Path(b.source_path).name}]\n\n")
            if ocr_text:
                parts.append(ocr_text)
            else:
                parts.append("（使用 read 工具识别此图片内容）")
            parts.append("\n\n---\n\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts), encoding="utf-8")


async def read_document_branch(
    *,
    call_id: str,
    raw_path: str,
    offset: Any,
    limit: Any,
    context: ToolContext,
    resolver: WorkspaceResolver,
) -> ToolResult:
    """Read a document file (.docx/.xlsx/.pptx/.pdf/.md) with text and inline
    image references. Pagination uses offset/limit (1-based line numbers),
    identical to the read tool's text path.

    Callers (ReadTool) must have verified ``_fmt_for_path(raw_path)`` already.
    """
    fmt = _fmt_for_path(raw_path)
    if fmt is None:
        return error_result(
            call_id,
            f"read：不支持的文档格式 '{Path(raw_path).suffix}'。"
            f"支持的格式：{', '.join(sorted(_ALL_SUPPORTED_SUFFIXES))}",
        )

    if context.abort.is_set():
        return error_result(call_id, "read：已中止")

    try:
        full_path = resolver.resolve_within(
            context.workspace_path, raw_path, session_root=session_root(context),
        )
    except WorkspaceBoundaryError as exc:
        return error_result(call_id, str(exc))

    try:
        runtime = require_runtime(context)
        r_result = await runtime.read_bytes(
            str(full_path),
            template_id=ws_kind(context),
            workspace_id=context.workspace_id,
            workspace_root=ws_root(context),
        )
        if not r_result.success or r_result.content is None:
            return error_result(call_id, f"read：{r_result.error}")
        raw = r_result.content  # bytes

        if fmt in ("xlsx", "pptx"):
            # 结构化 Office 格式：保留编辑锚点
            text = await asyncio.to_thread(read_office_text, raw, fmt)
        else:
            # 图文文档格式：按序提取文本+图片
            blocks = await asyncio.to_thread(
                extract_document_blocks,
                raw,
                fmt,
                source_rel_path=raw_path,
                workspace_path=context.workspace_path,
            )
            text = _blocks_to_text(blocks)
            # 写 analysis.md（同一文件不重复生成）
            analysis_path = analysis_md_path(str(context.workspace_path), str(full_path))
            if not analysis_path.exists() and not str(analysis_path).startswith("/nonexistent"):
                await asyncio.to_thread(
                    _write_analysis_md, blocks, analysis_path,
                    workspace_path=str(context.workspace_path),
                )
    except (OSError, Exception) as exc:  # noqa: BLE001 - surface any parse error
        return error_result(call_id, f"read：解析 {raw_path} 失败：{exc}")

    # 分页：按行切分，与 read 文本路径相同的 offset/limit 语义
    lines = text.split("\n")
    total_lines = len(lines)

    start = (offset - 1) if isinstance(offset, int) and offset > 0 else 0
    end = start + limit if isinstance(limit, int) and limit > 0 else start + _PAGE_LINES

    if start >= total_lines and total_lines > 0:
        return text_result(
            call_id,
            f"[read：offset {start + 1} 已超出文档末尾 "
            f"（共 {total_lines} 行）。无可显示内容。]",
        )

    window = "\n".join(lines[start:end])
    shown_from = start + 1
    shown_to = min(end, total_lines)

    header = f"[read：{raw_path} — 文档，第 {shown_from}-{shown_to} 行，共 {total_lines} 行]\n\n"

    if end < total_lines:
        window += (
            f"\n[当前显示第 {shown_from}-{shown_to} 行，共 {total_lines} 行。"
            f"继续读取请调用：read(file_path={raw_path!r}, offset={shown_to + 1})]\n"
        )

    return text_result(
        call_id,
        header + window,
    )