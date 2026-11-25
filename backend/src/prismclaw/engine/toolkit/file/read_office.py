"""read 工具的文档分支：按页解析、按页缓存、带续读游标。

分页维度是「文档页」，与文本路径的行语义分开（DD-1）。同一文档再次读取命中
会话级缓存，不重新解析。引用坐标形如 ``doc:<hash8>/p3/b7@text``，可直接用于
``after`` 参数续读。
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from prismclaw.engine.review.changes.office_engines import read_office_text
from prismclaw.engine.toolkit.builtin.file_helpers import (
    _READ_DEFAULT_PAGE_LINES,
    session_root,
)
from prismclaw.engine.toolkit.builtin.shared_tool_helpers import require_runtime, ws_kind, ws_root
from prismclaw.engine.toolkit.file.read_image import OCR_CONCURRENCY, ocr_image
from prismclaw.engine.toolkit.file.workspace import WorkspaceBoundaryError, WorkspaceResolver
from prismclaw.engine.toolkit.office.document_reader import (
    DOC_FORMATS,
    PAGELESS_FORMATS,
    PERMANENT_FAILURE_PREFIXES,
    STRUCTURED_OFFICE_FORMATS,
    TIERS,
    Block,
    Cursor,
    build_ocr_page_blocks,
    cache_dir,
    close_doc,
    content_hash8,
    detected_scanned_pages,
    ensure_cache,
    load_page,
    open_doc,
    page_count,
    parse_locator,
    parse_page,
    parse_pdf_page_with_modality,
    record_page_modality,
    record_vision_attempt,
    render_block,
    render_blocks,
    render_blocks_by_page,
    render_page_header,
    render_page_image,
    render_page_images,
    save_page,
    structured_office_images,
    vision_attempted,
)
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.schema.content.content_types import ToolResult

# 结构化 Office 格式（走 read_office_text 保留编辑锚点），不在按页模型内
_STRUCT_OFFICE_SUFFIXES = set(STRUCTURED_OFFICE_FORMATS)

_ALL_SUPPORTED_SUFFIXES = set(DOC_FORMATS) | _STRUCT_OFFICE_SUFFIXES

# 单次调用的 OCR 总预算。ReadTool 上限 120s，留余量给解析与落盘。
# 到点停止派发新页，已完成的页照常返回。
_OCR_TOTAL_BUDGET = 100.0

if TYPE_CHECKING:
    from prismclaw.engine.toolkit.office.document_reader import DocLike


def _fmt_for_path(path: str) -> str | None:
    """根据路径后缀返回解析格式键，不支持返回 None。"""
    ext = Path(path).suffix.lower()
    if ext in DOC_FORMATS:
        return DOC_FORMATS[ext]
    if ext in STRUCTURED_OFFICE_FORMATS:
        return STRUCTURED_OFFICE_FORMATS[ext]
    return None


def _resolve_offset(
    *,
    fmt: str,
    offset: object,
    after: object,
    call_id: str,
    raw_path: str,
) -> tuple[int, str, Cursor | None, ToolResult | None]:
    """把 offset / after 归一为 (页码, tier, 起始游标, 错误结果)。

    - ``after`` 优先：解析定位符，得到目标页与块号
    - ``offset`` 是页码简写；对 ``PAGELESS_FORMATS`` 传 >1 报错（不假装有页）
    """
    tier = "text"
    start: Cursor | None = None
    if isinstance(after, str) and after.strip():
        try:
            cursor = parse_locator(after)
        except ValueError as exc:
            return 1, tier, None, error_result(call_id, f"read：{exc}")
        tier = cursor.tier
        start = cursor
        return cursor.page, tier, start, None

    if isinstance(offset, int) and offset > 1:
        if fmt in PAGELESS_FORMATS:
            return (
                1,
                tier,
                None,
                error_result(
                    call_id,
                    f"read：{Path(raw_path).suffix} 无页概念，不支持 offset 分页；"
                    "如需跳过开头请直接用 read 读取全文。",
                ),
            )
        return offset, tier, None, None
    return 1, tier, start, None


async def read_document_branch(
    *,
    call_id: str,
    raw_path: str,
    offset: object,
    limit: object,
    after: object = None,
    tier: object = None,
    context: ToolContext,
    resolver: WorkspaceResolver,
) -> ToolResult:
    """读取文档：按页解析 + 缓存 + 续读游标。

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

    if isinstance(tier, str) and tier and tier not in TIERS:
        return error_result(call_id, f"read：未知档位 {tier!r}；可选 text / vision")
    requested_tier = tier if isinstance(tier, str) and tier else "text"

    try:
        full_path = resolver.resolve_within(
            context.workspace_path,
            raw_path,
            session_root=session_root(context),
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
        # 文档解析需要原始字节；文本内容说明 runtime 走了文本通道，无法解析。
        if not isinstance(r_result.content, bytes):
            return error_result(call_id, f"read：{raw_path} 未返回二进制内容，无法解析文档")
        raw = r_result.content

        if fmt in STRUCTURED_OFFICE_FORMATS.values():
            text = await asyncio.to_thread(read_office_text, raw, fmt)
            source_rel = _source_rel_path(full_path, context.workspace_path, raw_path)
            images = await asyncio.to_thread(
                structured_office_images,
                raw,
                fmt,
                workspace_path=context.workspace_path,
                source_rel_path=source_rel,
            )
            return _paged_text_result(
                call_id, raw_path, text, offset, limit, images=images, fmt=fmt
            )

        source_rel = _source_rel_path(full_path, context.workspace_path, raw_path)
        page, active_tier, start, offset_error = _resolve_offset(
            fmt=fmt,
            offset=offset,
            after=after,
            call_id=call_id,
            raw_path=raw_path,
        )
        if offset_error is not None:
            return offset_error
        # 显式 tier 参数优先于游标里的档位
        if isinstance(tier, str) and tier:
            active_tier = requested_tier

        return await asyncio.to_thread(
            _read_paged_document,
            raw=raw,
            fmt=fmt,
            raw_path=raw_path,
            source_rel=source_rel,
            workspace_path=context.workspace_path,
            page=page,
            tier=active_tier,
            start=start,
            limit=limit,
            call_id=call_id,
        )
    except Exception as exc:  # noqa: BLE001 - 第三方解析器的异常一律转为工具错误
        return error_result(call_id, f"read：解析 {raw_path} 失败：{exc}")


def _source_rel_path(full_path: Path, workspace_path: Path, raw_path: str) -> str:
    try:
        return full_path.relative_to(workspace_path).as_posix()
    except ValueError:
        return raw_path


def _read_paged_document(
    *,
    raw: bytes,
    fmt: str,
    raw_path: str,
    source_rel: str,
    workspace_path: Path,
    page: int,
    tier: str,
    start: Cursor | None,
    limit: object,
    call_id: str,
) -> ToolResult:
    """同步体：开文档 → 取页（缓存优先）→ 渲染。

    OCR 的网络等待发生在 :func:`_page_blocks` 里，且不在 ``_PDFIUM_LOCK`` 内。
    """
    target = cache_dir(workspace_path, raw, source_rel)
    hash8 = content_hash8(raw)
    deadline = time.monotonic() + _OCR_TOTAL_BUDGET

    # 游标绑定内容摘要：把 A 文档的 after 用在 B 文档上（或内容已被改写）
    # 时坐标会整体错位，与其静默返回错位内容，不如明确报错要求重读。
    if start is not None and start.hash8 != hash8:
        return error_result(
            call_id,
            "read：续读游标与当前文件不匹配（文件已变更或游标来自其它文件），"
            "请不带 after 重新读取",
        )

    doc = open_doc(raw, fmt, workspace_path=workspace_path, source_rel_path=source_rel)
    try:
        total_pages = page_count(doc, fmt)

        if page < 1 or page > total_pages:
            return error_result(
                call_id,
                f"read：页码 {page} 超出范围（共 {total_pages} 页）",
            )

        # tier=vision 的语义是「渲染整页图像交给视觉模型」，只有 PDF 能渲染；
        # 其余格式的文本已能直接提取，渲染没有意义（也避免把文本写进 vision 缓存）。
        if tier == "vision" and fmt != "pdf":
            return error_result(
                call_id,
                f"read：只有 PDF 支持 tier=vision；{Path(raw_path).suffix} 可直接提取文本",
            )

        ensure_cache(target, fmt=fmt, pages=total_pages)

        # 到这里 tier=vision 只可能是 PDF（上面已拦掉其余格式），批量语义成立。
        if tier == "vision" and start is None and isinstance(limit, int) and limit > 1:
            batch = True
            blocks, page_end, batch_note = _read_vision_pages(
                doc,
                target=target,
                workspace_path=workspace_path,
                source_rel=source_rel,
                page=page,
                limit=limit,
                total_pages=total_pages,
                deadline=deadline,
            )
        else:
            batch = False
            blocks = _page_blocks(
                doc,
                fmt=fmt,
                target=target,
                workspace_path=workspace_path,
                source_rel=source_rel,
                page=page,
                tier=tier,
                deadline=deadline,
            )
            page_end, batch_note = page, None
    finally:
        close_doc(doc, fmt)

    dropped: Block | None = None
    if not batch and limit is not None and isinstance(limit, int) and limit > 0:
        if len(blocks) > limit:
            # 截断不是终点：记下第一个被丢弃的块，渲染后据此给续读游标，
            # 否则本页剩余块静默消失且没有任何继续读取的提示。
            dropped = blocks[limit]
        blocks = blocks[:limit]

    blocks = _with_scan_guidance(blocks, tier, vision_attempted(target))
    if batch:
        # 批量以页为窗口边界：不按字符切页，超预算停在最后整页。
        body, rendered_page, next_cursor = render_blocks_by_page(blocks, hash8=hash8, tier=tier)
        if rendered_page is not None:
            page_end = rendered_page
        elif next_cursor is not None:
            # 首屏连一页都放不下：实际只输出了这一页的一部分，页头不能声称整批。
            page_end = next_cursor.page
    else:
        body, next_cursor = render_blocks(blocks, hash8=hash8, tier=tier, start=start)
        if next_cursor is None and dropped is not None:
            next_cursor = Cursor(
                hash8=hash8, page=dropped.page, index=dropped.index, tier=tier
            )

    header = render_page_header(
        raw_path,
        page,
        total_pages,
        tier,
        page_end=page_end,
        ocr_pages=sorted({b.page for b in blocks if b.is_ocr and page <= b.page <= page_end}),
        scanned_pages=detected_scanned_pages(target),
    )
    out = [header, "", body] if body else [header, "", "（本页无内容）"]
    if batch_note:
        out.append("")
        out.append(batch_note)
    if next_cursor is not None:
        out.append("")
        out.append(f"[继续读取：read(file_path={raw_path!r}, after={str(next_cursor)!r})]")
    elif page_end < total_pages:
        out.append("")
        if tier == "vision":
            out.append(
                f"[下一页：read(file_path={raw_path!r}, offset={page_end + 1}, "
                f"limit={OCR_CONCURRENCY}, tier='vision')]"
            )
        else:
            out.append(f"[下一页：read(file_path={raw_path!r}, offset={page + 1})]")

    return text_result(call_id, "\n".join(out))


def _read_vision_pages(
    doc: DocLike,
    *,
    target: Path,
    workspace_path: Path,
    source_rel: str,
    page: int,
    limit: int,
    total_pages: int,
    deadline: float,
) -> tuple[list[Block], int, str | None]:
    """一次读取连续多页扫描件：整批渲染（锁内）→ 并发 OCR（锁外）。

    返回 ``(blocks, page_end, note)``。``blocks`` 按页顺序拼接；``note`` 是
    因页数上限或超时预算而降级时的说明，无降级为 ``None``。
    """
    requested_end = min(page + limit - 1, total_pages)
    page_end = min(requested_end, page + OCR_CONCURRENCY - 1)
    notes: list[str] = []
    if page_end < requested_end:
        notes.append(f"请求的 p{page}–p{requested_end} 超出单次上限 {OCR_CONCURRENCY} 页")

    pages = list(range(page, page_end + 1))
    blocks: list[Block] = []
    pending: list[int] = []

    for p in pages:
        cached = load_page(target, p, "vision")
        if cached is not None:
            blocks.extend(cached)
            continue
        text_blocks = load_page(target, p, "text")
        if text_blocks is None:
            # 有文本层的页不该外发给视觉模型：先解析文本层，肉眼可读就不用 OCR。
            text_blocks, modality = parse_pdf_page_with_modality(
                doc, p, workspace_path=workspace_path, source_rel_path=source_rel
            )
            record_page_modality(target, p, modality)
            save_page(target, p, "text", text_blocks)
        if _has_readable_text(text_blocks):
            blocks.extend(text_blocks)
            continue
        pending.append(p)

    if not pending:
        return blocks, page_end, ("；".join(notes) if notes else None)

    images, failures = render_page_images(doc, pending)
    for p, message in failures.items():
        blocks.append(Block(kind="unsupported", page=p, index=1, text=message))

    ocr_targets = [p for p in pending if p in images]
    if not ocr_targets:
        # 全部渲染失败：页级错误已在 blocks 里，直接返回。
        blocks.sort(key=lambda block: (block.page, block.index))
        return blocks, page_end, ("；".join(notes) if notes else None)
    if time.monotonic() >= deadline:
        # 预算已尽：不再派发，也不落缓存；未处理的页不计入响应。
        notes.append(f"p{ocr_targets[0]} 起的 {len(ocr_targets)} 页因超时预算未处理")
        blocks.sort(key=lambda block: (block.page, block.index))
        return blocks, page_end, "；".join(notes)

    results: dict[int, list[Block]] = {}

    def _run(p: int) -> tuple[int, list[Block]]:
        try:
            return p, _ocr_from_jpeg(
                p,
                images[p],
                target=target,
                workspace_path=workspace_path,
                source_rel=source_rel,
                deadline=deadline,
            )
        except Exception as exc:  # noqa: BLE001 - 单页落盘/组装失败不应丢弃整批
            return p, [
                Block(
                    kind="unsupported",
                    page=p,
                    index=1,
                    text=f"该页 OCR 结果处理失败（{type(exc).__name__}）：{exc}",
                )
            ]

    with ThreadPoolExecutor(max_workers=OCR_CONCURRENCY) as pool:
        for p, page_blocks in pool.map(_run, ocr_targets):
            results[p] = page_blocks

    for p in ocr_targets:
        if p in results:
            blocks.extend(results[p])
    blocks.sort(key=lambda block: (block.page, block.index))
    return blocks, page_end, ("；".join(notes) if notes else None)


def _ocr_from_jpeg(
    page: int,
    jpeg: bytes,
    *,
    target: Path,
    workspace_path: Path,
    source_rel: str,
    deadline: float,
) -> list[Block]:
    """转录一页图像并落缓存。临时失败不写缓存，重试可再次调用模型。"""
    record_vision_attempt(target, page)
    text, error = ocr_image(jpeg, deadline=deadline)
    if error:
        return [Block(kind="unsupported", page=page, index=1, text=f"该页 OCR 失败：{error}")]
    blocks = build_ocr_page_blocks(
        page, text, jpeg, workspace_path=workspace_path, source_rel_path=source_rel
    )
    save_page(target, page, "vision", blocks)
    return blocks


def _page_blocks(
    doc: DocLike,
    *,
    fmt: str,
    target: Path,
    workspace_path: Path,
    source_rel: str,
    page: int,
    tier: str,
    deadline: float,
) -> list[Block]:
    """取页块（``tier == "vision"`` 只可能是 PDF，调用方已保证）。

    vision 档：优先 vision 缓存，其次复用已解析的 text 块（不重复渲染）。
    text 档：命中 text 缓存即返回；若该页无文本层而 vision 缓存已存在，用 vision
    结果——否则用户此前花代价识别的扫描页会被静默丢弃。

    渲染在 ``_PDFIUM_LOCK`` 内（约 10ms），OCR 在锁外（2–4s）：锁边界不能混。
    """
    if tier == "vision":
        cached = load_page(target, page, "vision")
        if cached is not None:
            return cached
        # 有文本层的页不渲染、不外发：显式 vision 的语义是「读不到就渲染」。
        text_blocks = load_page(target, page, "text")
        if text_blocks is None:
            text_blocks, modality = parse_pdf_page_with_modality(
                doc, page, workspace_path=workspace_path, source_rel_path=source_rel
            )
            record_page_modality(target, page, modality)
            save_page(target, page, "text", text_blocks)
        if _has_readable_text(text_blocks):
            return text_blocks
        return _ocr_pdf_page(
            doc,
            target=target,
            workspace_path=workspace_path,
            source_rel=source_rel,
            page=page,
            deadline=deadline,
        )

    cached = load_page(target, page, "text")
    if cached is not None and _has_readable_text(cached):
        return cached
    vision_blocks = load_page(target, page, "vision")
    if vision_blocks is not None:
        return vision_blocks
    if cached is not None:
        return cached

    if fmt == "pdf":
        blocks, modality = parse_pdf_page_with_modality(
            doc,
            page,
            workspace_path=workspace_path,
            source_rel_path=source_rel,
        )
        record_page_modality(target, page, modality)
    else:
        blocks = parse_page(
            doc,
            page,
            workspace_path=workspace_path,
            source_rel_path=source_rel,
        )
    save_page(target, page, tier, blocks)
    return blocks


def _ocr_pdf_page(
    doc: DocLike,
    *,
    target: Path,
    workspace_path: Path,
    source_rel: str,
    page: int,
    deadline: float,
) -> list[Block]:
    """渲染一页（锁内）→ 转录（锁外）→ 组装块。锁边界不能混。"""
    try:
        jpeg = render_page_image(doc, page)
    except ValueError as exc:
        return [Block(kind="unsupported", page=page, index=1, text=str(exc))]
    return _ocr_from_jpeg(
        page,
        jpeg,
        target=target,
        workspace_path=workspace_path,
        source_rel=source_rel,
        deadline=deadline,
    )


def _with_scan_guidance(
    blocks: list[Block],
    tier: str,
    attempted: set[int],
) -> list[Block]:
    """给 unsupported 块追加「下一步」指引（方案 §4.9 三态）。

    文案不能固化进缓存：同一页在「从未尝试 OCR」与「尝试过且失败」两种状态下，
    指引必须不同，否则模型会陷入 read→vision→失败→read 的死循环。
    """
    out: list[Block] = []
    for block in blocks:
        if block.kind != "unsupported":
            out.append(block)
            continue
        # 渲染失败/页面损坏是永久性问题，不能建议重试；只有 OCR 调用失败可重试。
        if block.text.startswith(PERMANENT_FAILURE_PREFIXES):
            guidance = ""
        elif tier == "vision":
            guidance = "OCR 未返回可用文本，可稍后重试。"
        elif block.page in attempted:
            guidance = (
                "该页 OCR 尝试失败（可能是临时错误），稍后可用 "
                "read(offset=页码, tier='vision') 重试。"
            )
        else:
            guidance = "可用 read(offset=页码, tier='vision') 识别。"
        out.append(
            Block(
                kind="unsupported",
                page=block.page,
                index=block.index,
                text=block.text + guidance,
            )
        )
    return out


def _has_readable_text(blocks: list[Block]) -> bool:
    return any(block.kind in ("text", "table") for block in blocks)


def _paged_text_result(
    call_id: str,
    raw_path: str,
    text: str,
    offset: object,
    limit: object,
    *,
    images: list[Block] | None = None,
    fmt: str = "",
) -> ToolResult:
    """结构化 Office（xlsx/pptx）按行分页，保持既有行语义。

    内嵌图片单独成节列出：它们是文档内容的一部分，但不在锚点文本里；
    只给路径不给 base64，模型按需 ``read`` 单张图走视觉识别。
    """
    lines = text.split("\n")
    total_lines = len(lines)
    start = (offset - 1) if isinstance(offset, int) and offset > 0 else 0
    end = (
        start + limit if isinstance(limit, int) and limit > 0 else start + _READ_DEFAULT_PAGE_LINES
    )

    if start >= total_lines and total_lines > 0:
        return text_result(
            call_id,
            f"[read：offset {start + 1} 已超出文档末尾（共 {total_lines} 行）。无可显示内容。]",
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
    if images:
        window += "\n" + _render_image_section(images, fmt=fmt)
    return text_result(call_id, header + window)


def _render_image_section(images: list[Block], *, fmt: str) -> str:
    """内嵌图片清单：只列路径与所属页，图片内容由模型按需 read。"""
    unit = "张幻灯片" if fmt == "pptx" else "个工作表"
    rows = [f"  {render_block(block)}（第 {block.page} {unit}）" for block in images]
    return f"[内嵌图片 {len(images)} 张（已按声明旋转角还原）：]\n" + "\n".join(rows)
