"""Attachment routing for agent turns.

This module is the single place where backend attachment handling is decided.
It normalizes raw payloads, preprocesses document files (text + images),
projects them into per-attachment units once, then renders the three channels
from that shared state:

- display: blocks rendered by the frontend (image identity, text, file cards)
- llm:     inline content, or self-sufficient references when content is not
           inlined (images never enter the model as native input: OCR text is
           inlined when available, otherwise a read reference is given)
- system:  the attachment policy note

State is computed once in ``project_blocks``; narration exists only in the
channel renderers, so no channel can assert content it never received.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from prismclaw.engine.toolkit.office.document_image_extract import extract_document_blocks
from prismclaw.schema.agent.data_types import FileBlock, ImageBlock, TextBlock

logger = logging.getLogger(__name__)

# OOXML 段落锚点前缀：document_image_extract 在每段前加 ``[w14:paraId] `` 供
# docx 编辑工具按段落定位；这是内部编辑句柄，对聊天展示无意义，display 渲染时剥离。
_PARA_ID_PREFIX_RE = re.compile(r"^\[[0-9A-Fa-f]{8}\]\s*")

_WORKSPACE_FILE_PATH_RE = re.compile(
    r"(?:(?:^|[\s(]))"
    r"(?!https?://)(?!file://)(?!/)(?![A-Za-z]:[\\/])"
    r"[.A-Za-z0-9_-]+(?:/[.A-Za-z0-9_-]+)+\.[A-Za-z0-9]{1,16}"
    r"(?=$|[\s)])",
    re.IGNORECASE,
)

_THREAD_POOL_MAX_WORKERS = 4
_SUPPORTED_DOC_FORMATS = {".docx", ".pdf", ".md", ".markdown", ".rst", ".xlsx", ".pptx"}

# 无 mime_type 时按扩展名推断（仅用于 ImageBlock 的 identity，真实 mime 由
# image_preprocess 从文件确定）。
_EXT_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _resolve_mime(mime_type: Any, source_path: str) -> str:
    """声明的 mime 优先；缺失时按扩展名推断（真实 mime 由文件本身确定）。"""
    if isinstance(mime_type, str) and mime_type.strip():
        return mime_type.strip()
    return _EXT_MIME.get(Path(source_path).suffix.lower(), "image/jpeg")


# 阈值各自独立，互不替代：
# - _MAX_DISPLAY_TEXT_CHARS：前端渲染上限（防 WebSocket 传输 + 前端崩溃）
# - _MAX_EXTRACT_FILE_SIZE：OOM 防御（read_bytes 前预检，防内存撑爆）
# - _ATTACHMENT_CHAR_BUDGET_FALLBACK：内联预算兜底（仅配置/求解失败时用，
#   不作为可调键——正常路径由 subcaps.attachment_token_ratio/cap 决定）
# 三者均非运营旋钮，不外置。
_MAX_DISPLAY_TEXT_CHARS = 50_000
_MAX_EXTRACT_FILE_SIZE = 100_000_000
_ATTACHMENT_CHAR_BUDGET_FALLBACK = 30_000


def _subcaps_of(context_budget: Any) -> Any:
    """取上下文预算的 subcaps；缺失时用 schema 默认值（子额度单一来源）。"""
    sub = getattr(context_budget, "subcaps", None)
    if sub is not None:
        return sub
    from prismclaw.schema.config.config import SubcapsSettings

    return SubcapsSettings()


def _compute_ratio_attachment_token_cap(input_budget: int, subcaps: Any) -> int:
    """ratio 模式附件 token 预算（SDD 4.3.4）：min(round(ratio·input_budget), cap)。"""
    return min(
        round(float(subcaps.attachment_token_ratio) * int(input_budget)),
        int(subcaps.attachment_token_cap),
    )


def _compute_attachment_char_budget(context_budget: Any = None) -> int:
    """根据上下文 token 预算动态计算附件内容的字符预算。

    mode=="ratio"：token_cap = min(ratio·input_budget, cap)，字符 = cap×1.5
    （ratio/cap 读 subcaps）。
    mode=="absolute"：沿用 history_tokens×0.6 的旧公式（零漂移）。
    """
    if context_budget is None:
        return _ATTACHMENT_CHAR_BUDGET_FALLBACK
    subcaps = _subcaps_of(context_budget)
    try:
        if getattr(context_budget, "mode", "absolute") == "ratio":
            from prismclaw.engine.compression.context_zones import resolve_runtime_zones

            input_budget = resolve_runtime_zones(
                budget=context_budget,
                window=int(getattr(context_budget, "window_tokens", 0) or 0),
                model_max_output=None,
                tools_wire=0,
                image_tokens=0,
            ).input_budget
            attachment_token_budget = _compute_ratio_attachment_token_cap(input_budget, subcaps)
            char_budget = int(attachment_token_budget * 1.5)
            return max(10_000, char_budget)

        if hasattr(context_budget, 'compute_history_budget'):
            history_tokens = context_budget.compute_history_budget(model_max_output=None)
        else:
            history_tokens = (
                context_budget.window_tokens
                - context_budget.system_zone_tokens
                - context_budget.dynamic_zone_tokens
                - context_budget.output_reserve_tokens
            )
        attachment_token_budget = max(0, int(history_tokens * 0.6))
        char_budget = int(attachment_token_budget * 1.5)
        return max(10_000, char_budget)
    except Exception:
        return _ATTACHMENT_CHAR_BUDGET_FALLBACK


@dataclass
class AttachmentRoutingDecision:
    """Resolved attachment plan for one user turn.

    图片不直接进主上下文（实证 2048²≈23000 tokens 必撑爆）：有 OCR 文本时以
    文本内联，否则给 read_image 引用——故不存在 vision 能力检测或模型切换。
    ``downgraded_image_count`` 是本轮以引用形式交给 read_image 的图片数。
    """

    display_content: Any
    llm_content: Any
    attachment_guidance: str | None
    downgraded_image_count: int = 0


@dataclass
class NormalizedAttachmentBlocks:
    """Normalized attachment collections while preserving original order."""

    ordered: list[FileBlock | ImageBlock]
    images: list[ImageBlock]
    files: list[FileBlock]


def _image_display(block: ImageBlock) -> ImageBlock:
    """前端视图：保留 identity（source_path/mime_type），data 清空（不进 LLM）。"""
    return ImageBlock(
        type="image",
        data="",
        mime_type=block.mime_type,
        source_path=block.source_path,
    )


def _workspace_target(rel_path: str, workspace_path: Path) -> Path | None:
    """payload 路径 → 工作区内真实路径；越界（``..`` / 绝对路径 / 外链符号链接）返回 None。

    附件路径来自客户端 payload，必须在此边界收敛：否则可以把工作区外的任意文件
    读进模型上下文或送去 OCR。上传管线写盘时已保证相对路径，这里做读取侧同等校验。
    """
    try:
        root = workspace_path.resolve()
        target = (root / rel_path).resolve()
    except (OSError, RuntimeError):
        return None
    return target if target == root or root in target.parents else None


def normalize_attachment_blocks(
    raw_attachments: list[Any], workspace_path: Path
) -> NormalizedAttachmentBlocks:
    """Coerce mixed transport payloads into typed blocks without losing order.

    Payloads whose path escapes the workspace are dropped — they cannot be read,
    OCR'd, or referenced by any tool, so announcing them would be a false claim.
    """

    ordered: list[FileBlock | ImageBlock] = []
    images: list[ImageBlock] = []
    files: list[FileBlock] = []
    for item in raw_attachments:
        if isinstance(item, ImageBlock):
            source_path = item.source_path
            if source_path and _workspace_target(source_path, workspace_path):
                ordered.append(item)
                images.append(item)
            continue
        if isinstance(item, FileBlock):
            if _workspace_target(item.path, workspace_path):
                ordered.append(item)
                files.append(item)
            continue
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "image":
            source_path = item.get("source_path")
            # 图片一律按工作区路径定位（主模型不收图片，OCR 与 read_image 都需要
            # path）；无 path / 越界的 payload 无法被任何通道使用，直接忽略。
            if (
                isinstance(source_path, str)
                and source_path.strip()
                and _workspace_target(source_path.strip(), workspace_path)
            ):
                sp = source_path.strip()
                mime_type = item.get("mime_type")
                img = ImageBlock(
                    type="image",
                    data="",
                    mime_type=_resolve_mime(mime_type, sp),
                    source_path=sp,
                )
                images.append(img)
                ordered.append(img)
        elif item_type == "file":
            path = item.get("path")
            if (
                isinstance(path, str)
                and path.strip()
                and _workspace_target(path.strip(), workspace_path)
            ):
                name = item.get("name")
                mime_type = item.get("mime_type")
                size_bytes = item.get("size_bytes")
                resolved_mime_type = (
                    mime_type.strip()
                    if isinstance(mime_type, str) and mime_type.strip()
                    else None
                )
                fb = FileBlock(
                    type="file",
                    path=path.strip(),
                    name=name.strip() if isinstance(name, str) else "",
                    mime_type=resolved_mime_type,
                    size_bytes=size_bytes if isinstance(size_bytes, int) else None,
                )
                files.append(fb)
                ordered.append(fb)
    return NormalizedAttachmentBlocks(ordered=ordered, images=images, files=files)


def _message_mentions_uploaded_files(user_message: str) -> bool:
    """消息里是否引用了工作区文件路径（据此决定是否需要附件策略）。"""
    text = user_message.strip()
    if not text:
        return False
    return _WORKSPACE_FILE_PATH_RE.search(text) is not None


def _attachment_policy(
    *,
    has_image_attachments: bool,
    has_file_attachments: bool,
    user_message: str,
) -> str | None:
    """系统通道：附件相关约束（工具路由已在 system_prompt 的静态段，不重复）。

    只写三条约束：未内联内容必须先取后述、意图优先级、图片路由是设计而非
    模型能力限制。完整性由渲染层的事实陈述承担，此处不做任何完整性断言。
    """
    has_uploaded_files = has_file_attachments or _message_mentions_uploaded_files(user_message)
    if not has_image_attachments and not has_uploaded_files:
        return None
    return "\n".join(
        [
            "## 附件处理",
            "- 标记为未内联的条目（例如 '[Image #1/3: …]'、'[Document …]'、"
            "'[File …]'）不在你的上下文中。描述之前必须先调用对应工具，"
            "绝不要声称已读取尚未获取的内容。",
            "- 若附件本身写明任务，或用户消息已明确隐含任务，请直接执行，不要询问。"
            "只有当意图确实含糊时才用 ask_user_question 提问（选项即可能的理解，"
            "推荐项放首位），不要在正文里罗列选项。",
            "- 图片通过 read 工具路由是设计使然，并非模型能力限制。",
        ]
    )


def _extract_file_blocks(
    fb: FileBlock,
    workspace_path: Path,
) -> Sequence[TextBlock | ImageBlock | FileBlock]:
    """Extract text and image blocks from a single file attachment.

    On error, unsupported format, or oversized file, returns the original
    FileBlock so the frontend still renders the attachment card. 失败原因不进
    内容——transport 层错误不进入模型上下文，模型按引用路径 read 时才会看到
    真实错误（单一事实来源）。

    Returns ``Sequence``（而非 ``list``）因为成功路径来自
    ``extract_document_blocks`` 的 ``list[TextBlock | ImageBlock]``，失败路径是
    ``FileBlock`` 列表——协变类型让两者共用同一签名。
    """
    try:
        full_path = workspace_path / fb.path
        if not full_path.exists():
            return [fb]

        ext = full_path.suffix.lower()
        if ext not in _SUPPORTED_DOC_FORMATS:
            # Unsupported format — preserve FileBlock for frontend rendering
            # (consistent with image downgrading). LLM can use ReadTool if needed.
            return [fb]

        # OOM 预检：超 100MB 不 read_bytes，直接返回 FileBlock 供前端渲染。
        # 4 个 worker 并发时峰值约 800MB–1.2GB，故阈值与并发度需一起看。
        if full_path.stat().st_size > _MAX_EXTRACT_FILE_SIZE:
            return [fb]

        raw = full_path.read_bytes()
        if ext in (".xlsx", ".pptx"):
            # 结构化 Office 格式：走 read_office_text 保留编辑锚点
            from prismclaw.engine.review.changes.office_engines import read_office_text
            text = read_office_text(raw, ext.lstrip("."))
            return [TextBlock(type="text", text=text)]
        fmt_map = {
            ".docx": "docx",
            ".pdf": "pdf",
            ".md": "markdown",
            ".markdown": "markdown",
            ".rst": "markdown",
        }
        fmt = fmt_map.get(ext, ext.lstrip("."))
        blocks = extract_document_blocks(
            raw,
            fmt,
            source_rel_path=fb.path,
            workspace_path=workspace_path,
        )
        return blocks
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to preprocess {fb.path}: {exc}")
        return [fb]


def _preprocess_file_attachments(
    file_blocks: list[FileBlock],
    workspace_path: Path,
) -> dict[str, Sequence[TextBlock | ImageBlock | FileBlock]]:
    """Preprocess file attachments in parallel using ThreadPoolExecutor.

    Returns a dict mapping original file path to extracted blocks.
    Files that fail to parse or have unsupported format preserve the
    original FileBlock for frontend rendering.
    """
    results: dict[str, Sequence[TextBlock | ImageBlock | FileBlock]] = {}

    if not file_blocks:
        return results

    with ThreadPoolExecutor(max_workers=_THREAD_POOL_MAX_WORKERS) as executor:
        future_to_block = {
            executor.submit(_extract_file_blocks, fb, workspace_path): fb
            for fb in file_blocks
        }
        for future in as_completed(future_to_block):
            fb = future_to_block[future]
            try:
                blocks = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Failed to preprocess {fb.path}: {exc}")
                blocks = [fb]
            results[fb.path] = blocks

    return results


def _read_image_bytes_from_workspace(source_path: str, workspace_path: Path) -> bytes | None:
    """直接从本地文件系统读取图片字节。"""
    full_path = workspace_path / source_path
    try:
        return full_path.read_bytes()
    except OSError:
        return None


def _batch_ocr_attachments(
    direct_images: list,
    extracted_map: dict,
    workspace_path: Path,
    max_concurrent: int = 5,
    timeout_per_image: int = 30,
) -> dict[str, str]:
    """并发 OCR 所有图片，返回 {source_path: ocr_text}。"""
    from prismclaw.engine.toolkit.file.read_image import (
        _DEFAULT_QUESTION,
        _recognize_from_bytes,
    )

    # 收集所有图片（已有文本的不重复识别，避免覆盖已知内容）
    images_to_ocr: list[tuple[str, bytes]] = []
    for img in direct_images:
        if img.source_path and not img.text:
            data = _read_image_bytes_from_workspace(img.source_path, workspace_path)
            if data:
                images_to_ocr.append((img.source_path, data))
    for blocks in extracted_map.values():
        for b in blocks:
            if hasattr(b, 'source_path') and b.source_path and not getattr(b, 'text', None):
                data = _read_image_bytes_from_workspace(b.source_path, workspace_path)
                if data:
                    images_to_ocr.append((b.source_path, data))

    if not images_to_ocr:
        return {}

    # 并发 OCR
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {}
        for path, data in images_to_ocr:
            future = pool.submit(
                _recognize_from_bytes, path, data, _DEFAULT_QUESTION,
                str(workspace_path),
            )
            futures[future] = path

        try:
            for future in as_completed(futures, timeout=max_concurrent * timeout_per_image):
                path = futures[future]
                try:
                    text, _error = future.result(timeout=timeout_per_image)
                    if text:  # 空结果视为识别失败：不写入，该图降级为引用
                        results[path] = text
                except Exception:
                    logger.debug("OCR failed for %s, will degrade to note", path)
        except TimeoutError:
            logger.warning(
                "Batch OCR timed out, %d of %d images completed",
                len(results),
                len(futures),
            )
            # 取消未完成的 future
            for f in futures:
                f.cancel()

    return results


def _apply_ocr_to_blocks(
    direct_images: list,
    extracted_map: dict,
    ocr_results: dict[str, str],
) -> None:
    """将 OCR 结果回填到 ImageBlock.text。"""
    for img in direct_images:
        if img.source_path in ocr_results:
            img.text = ocr_results[img.source_path]
    for blocks in extracted_map.values():
        for b in blocks:
            if hasattr(b, 'text') and hasattr(b, 'source_path') and b.source_path in ocr_results:
                b.text = ocr_results[b.source_path]


# ---------------------------------------------------------------------------
# 附件投影：以“附件”为决策单元，状态只算一次
#
#   AttachmentUnit   一个附件（顶层图片 / 文档 / 未能提取的文件）的终态，
#                    同时持有 display 块与可内联内容块
#   project_blocks   遍历 ordered → 按附件聚合 → AttachmentProjection
#
# 设计要点：
#   - display 保 ImageBlock identity 供前端；llm 只用 content_blocks
#   - 文档的多块内容共享同一附件状态，避免逐块降级产生重复引用
#   - 是否内联由本轮 inline_chars 一次性决定（见 route_attachments_for_turn）
# ---------------------------------------------------------------------------
AttachmentKind = Literal["image", "document", "file"]


@dataclass
class AttachmentUnit:
    """一个附件在本轮中的投影。

    - display_blocks：前端视图块（图片保留 identity、文本按显示上限截断）
    - content_blocks：可进入 llm 的内容块；为空表示只能以引用路径呈现
    - image_refs：内嵌图片 [(path, 全局序号, OCR 字符数)]；内联模式下 OCR 已达上下文的
      只差引用，超预算模式下全部需要引用
    - chars：可内联进 llm 的字符数（含图片 OCR 文本块），预算前口径
    """

    path: str
    kind: AttachmentKind
    image_index: int = 0  # 顶层图片在本次上传中的 1-based 序号
    chars: int = 0
    display_blocks: list = field(default_factory=list)
    content_blocks: list = field(default_factory=list)
    image_refs: list[tuple[str, int, int]] = field(default_factory=list)


@dataclass
class AttachmentProjection:
    """一轮附件的投影终态——三个通道共享的唯一状态。"""

    entries: list[AttachmentUnit] = field(default_factory=list)
    image_total: int = 0
    ocr_image_count: int = 0

    @property
    def inline_chars(self) -> int:
        """全量内联所需字符数（预算前，含图片 OCR 文本）。"""
        return sum(entry.chars for entry in self.entries)


def _count_images(
    ordered: list[FileBlock | ImageBlock],
    extracted_map: dict[str, Sequence[Any]],
) -> int:
    """统计图片总数（顶层图片 + 文档内嵌图片）。

    必须与 project_blocks 的遍历口径一致（同一 path 重复出现时按出现次数计），
    否则引用里的 "第 i/total" 会出现 i > total。
    """
    total = 0
    for block in ordered:
        parts = (block,) if isinstance(block, ImageBlock) else extracted_map.get(block.path, ())
        total += sum(1 for part in parts if isinstance(part, ImageBlock))
    return total


def _image_ocr_text(block: ImageBlock) -> TextBlock:
    """图片 OCR 文本的内联视图（带文件名标签，便于对应用户所说的“第 N 张”）。"""
    label = f"[图片: {Path(block.source_path or 'unknown').name}]"
    return TextBlock(type="text", text=f"{label}\n{block.text}")


def project_blocks(
    ordered: list[FileBlock | ImageBlock],
    extracted_map: dict[str, Sequence[Any]],
) -> AttachmentProjection:
    """附件投影流水线：ordered + extracted_map → AttachmentProjection。

    每个附件（顶层图片 / 文档 / 未能提取的文件）成为一个 AttachmentUnit；
    文档的多块内容聚合到同一 unit，内嵌图片按文档顺序参与统一序号。
    kind 由“是否真有可内联内容”派生，不做额外状态标记。
    """
    total_images = _count_images(ordered, extracted_map)
    entries: list[AttachmentUnit] = []
    image_index = 0
    ocr_images = 0

    for block in ordered:
        if isinstance(block, ImageBlock):
            image_index += 1
            unit = AttachmentUnit(
                path=block.source_path or "", kind="image", image_index=image_index
            )
            unit.display_blocks.append(_image_display(block))
            if block.text:
                ocr_block = _image_ocr_text(block)
                unit.content_blocks.append(ocr_block)
                unit.chars += len(ocr_block.text)
                ocr_images += 1
            entries.append(unit)
            continue

        unit = AttachmentUnit(path=block.path, kind="document")
        for extracted in extracted_map.get(block.path, ()):
            if isinstance(extracted, ImageBlock):
                image_index += 1
                # 文档内嵌图片只进 llm 通道（模型经 read_image 引用 / OCR 内联），
                # 不进 display：展示须与「用户实际上传」一致——用户传的是一份文件，
                # 而非它内部抽取出的若干图片，气泡里重复铺开属于冗余泄漏。
                if extracted.text:
                    ocr_block = _image_ocr_text(extracted)
                    unit.content_blocks.append(ocr_block)
                    unit.chars += len(ocr_block.text)
                    ocr_images += 1
                    if extracted.source_path:
                        unit.image_refs.append(
                            (extracted.source_path, image_index, len(ocr_block.text))
                        )
                elif extracted.source_path:
                    unit.image_refs.append((extracted.source_path, image_index, 0))
            elif isinstance(extracted, TextBlock):
                if not extracted.text.strip():
                    # 空文本不是内容（如纯图片 pptx）：不让空块占位，按未提取处理
                    continue
                # 同上：文档正文只进 llm 通道（含 paraId 编辑锚点），不进 display。
                unit.content_blocks.append(extracted)
                unit.chars += len(extracted.text)
            else:
                # 提取失败时 _extract_file_blocks 回退为原 FileBlock：只上前端卡片
                unit.display_blocks.append(extracted)
        # 源文档文件卡：展示与「用户实际上传」一致——用户传的是一份文件，display
        # 通道只出这张源卡（可点击/下载），文档内部抽取的图片+正文只进 llm 通道，
        # 不泄漏到气泡（Issue C 修复后进一步收敛：不再把解析内容冗余铺开）。
        # 仅当显示流里已含该源卡（提取失败回退分支已追加原 FileBlock）时不重复添加。
        _has_source_card = any(
            getattr(b, "type", None) == "file" and getattr(b, "path", None) == block.path
            for b in unit.display_blocks
        )
        if not _has_source_card:
            unit.display_blocks.insert(
                0,
                FileBlock(
                    type="file",
                    path=block.path,
                    name=block.name,
                    mime_type=block.mime_type,
                    size_bytes=block.size_bytes,
                    is_source_document=True,
                ),
            )
        if not unit.content_blocks and not unit.image_refs:
            # 不支持格式 / 超 OOM 阈值 / 未提取：模型侧只能按路径引用
            unit.kind = "file"
        entries.append(unit)

    return AttachmentProjection(
        entries=entries,
        image_total=total_images,
        ocr_image_count=ocr_images,
    )


def _image_reference_note(path: str, index: int, total: int, *, chars: int) -> str:
    """图片引用 note：序号 + 真实 path + read + 未内联原因（压缩后仍自足）。"""
    name = Path(path).name if path else "image"
    reason = f"OCR 文本（{chars} 字符）未内联" if chars else "无可用 OCR 文本"
    return (
        f"[Image #{index}/{total}: {name} ({path or '(unknown path)'}) — {reason}；"
        "请用 read 工具读取该路径以查看内容。]"
    )


def _reference_lines(
    unit: AttachmentUnit, *, image_total: int, include_self: bool, ocr_inlined: bool
) -> list[str]:
    """附件引用行：真实 path + 正确工具 + 未内联原因（压缩丢上下文后仍自足）。

    include_self=False 表示该附件自身内容已内联，只需引用它的内嵌图片。
    ocr_inlined=True 表示带 OCR 文本的内嵌图片已在上下文里，无需再引用。
    """
    lines: list[str] = []
    if include_self:
        if unit.kind == "image":
            lines.append(
                _image_reference_note(unit.path, unit.image_index, image_total, chars=unit.chars)
            )
        elif unit.kind == "document":
            if unit.content_blocks:
                lines.append(
                    f"[Document {unit.path} — 已提取 {unit.chars} 字符，但本轮未内联；"
                    "请用 read 工具读取该路径以查看内容。]"
                )
            else:
                lines.append(
                    f"[Document {unit.path} — 未提取到文本；"
                    "请见下方图片引用。]"
                )
        else:
            lines.append(f"[File {unit.path} — 未提取到文本；请用 read 工具读取该路径以查看内容。]")
    lines.extend(
        _image_reference_note(path, index, image_total, chars=chars)
        for path, index, chars in unit.image_refs
        if not (chars and ocr_inlined)
    )
    return lines


def _reference_manifest(projection: AttachmentProjection, *, char_budget: int) -> str:
    """超预算时的引用清单：每附件（含未内联的内嵌图片）一条 path + 工具，上限为预算一半。"""
    header = (
        "[本轮附件内容超出内联预算——下方仅列出引用；请用 read 工具读取你需要的内容：]"
    )
    max_chars = int(char_budget * 0.5)
    lines = [header]
    used = len(header)
    omitted = 0
    for unit in projection.entries:
        for note in _reference_lines(
            unit,
            image_total=projection.image_total,
            include_self=True,
            ocr_inlined=False,
        ):
            if used + len(note) + 1 > max_chars:
                omitted += 1
                continue
            lines.append(note)
            used += len(note) + 1
    if omitted:
        lines.append(f"[另有 {omitted} 条附件引用因预算限制未列出。]")
    return "\n".join(lines)


def _turn_facts(projection: AttachmentProjection, *, inlined: bool) -> str:
    """只陈述可验证事实（数量与是否在上下文内），不做完整性断言。"""
    facts: list[str] = []
    if projection.image_total:
        ocr = projection.ocr_image_count if inlined else 0
        facts.append(f"图片 {projection.image_total} 张，其中 {ocr} 张的 OCR 文本已在上下文内")
    documents = [unit for unit in projection.entries if unit.kind == "document"]
    if documents:
        readable = sum(1 for unit in documents if unit.chars) if inlined else 0
        facts.append(f"文档 {len(documents)} 个，其中 {readable} 个已提取文本并在上下文内")
    files = sum(1 for unit in projection.entries if unit.kind == "file")
    if files:
        facts.append(f"文件 {files} 个，均未提取到文本")
    return "[附件事实：" + "；".join(facts) + "]"


def _render_llm(
    projection: AttachmentProjection,
    *,
    inlined: bool,
    char_budget: int,
) -> list[Any]:
    """渲染 llm 通道：内联模式按附件展开内容；超预算时只给引用清单。"""
    if not projection.entries:
        return []
    if not inlined:
        return [
            TextBlock(
                type="text",
                text=_reference_manifest(projection, char_budget=char_budget),
            )
        ]
    blocks: list[Any] = []
    for unit in projection.entries:
        blocks.extend(unit.content_blocks)
        for note in _reference_lines(
            unit,
            image_total=projection.image_total,
            include_self=not unit.content_blocks,
            ocr_inlined=True,
        ):
            blocks.append(TextBlock(type="text", text=note))
    return blocks


def _resolve_content(blocks: list[Any]) -> Any:
    """投影 block 列表 → 最终内容值（空列表 → 空字符串）。

    display 与 llm 各自独立调用本函数，杜绝交叉误用（display 截断文本不得
    污染 llm）。单 TextBlock 直接取 .text，避免无谓的列表包装。
    """
    if not blocks:
        return ""
    if len(blocks) == 1 and isinstance(blocks[0], TextBlock) and blocks[0].type == "text":
        return blocks[0].text
    return blocks


def route_attachments_for_turn(
    *,
    user_message: str,
    raw_attachments: list[Any],
    workspace_path: Path,
    context_budget: Any = None,  # ContextBudgetConfig | None
    display_user_message: str | None = None,
) -> AttachmentRoutingDecision:
    """Resolve the backend attachment route for a single turn.

    三通道契约：
    - display：附件块（图片保留 identity、文件卡片）+ 用户消息；无消息时不附加冗余占位文字（缩略图/文件卡即为指示）
    - llm：附件内容（可内联时）或自足引用（超预算时），再拼用户消息
    - guidance：附件策略（未取先述禁令 / 意图优先级 / 图片路由是设计）

    ``display_user_message`` 是落树/回放用的原样正文（web 端可能含内联卡片
    标记），与 ``user_message``（已还原成模型可读文本）分离：显示保真，模型
    上下文不受标记污染。

    图片不进入主模型上下文（实证 2048²≈23000 tokens 必撑爆）：有 OCR 文本时以
    文本内联，无 OCR 时给 read_image 引用；故不存在视觉能力检测或模型切换。

    ``workspace_path`` is required for document preprocessing.
    """

    # 1. Normalize（越界路径在此丢弃）
    normalized = normalize_attachment_blocks(raw_attachments, workspace_path)
    ordered_attachments = normalized.ordered
    has_file_attachments = bool(normalized.files)

    # 2. Preprocess file attachments (documents) — OOM 预检在 _extract_file_blocks 内
    extracted_map: dict[str, Sequence[TextBlock | ImageBlock | FileBlock]] = {}
    if has_file_attachments:
        extracted_map = _preprocess_file_attachments(normalized.files, workspace_path)

    # 3. 批量 OCR（并发，查缓存优先）
    ocr_results = _batch_ocr_attachments(
        direct_images=normalized.images,
        extracted_map=extracted_map,
        workspace_path=workspace_path,
    )

    # 4. 回填 OCR 结果到 ImageBlock
    _apply_ocr_to_blocks(normalized.images, extracted_map, ocr_results)

    # 5. 投影：状态只算一次（附件为单元，display/content 同时产出）
    projection = project_blocks(ordered_attachments, extracted_map)

    # 6. 预算：渲染前一次性决定 llm 通道模式（不做渲染后重建）
    char_budget = _compute_attachment_char_budget(context_budget)
    inlined = projection.inline_chars <= char_budget

    # 7. 渲染三通道
    display_blocks = [
        block for unit in projection.entries for block in unit.display_blocks
    ]
    llm_blocks = _render_llm(projection, inlined=inlined, char_budget=char_budget)

    display_message = display_user_message if display_user_message is not None else user_message
    if display_message and display_message.strip():
        # 有文字说明：附件内容在前，消息原样透传，不附加任何指令
        display_blocks.append(TextBlock(type="text", text=display_message))
    if user_message and user_message.strip():
        llm_blocks.append(TextBlock(type="text", text=user_message))
    elif projection.entries:
        # 无文字说明：llm 通道补可验证事实；display 通道**不再**附加「已上传附件」
        # 之类的冗余占位文字——附件缩略图/文件卡本身已是「已上传」的可视指示，
        # 多余文字只会在气泡里多出一串无价值提示。
        llm_blocks.append(
            TextBlock(type="text", text=_turn_facts(projection, inlined=inlined))
        )

    return AttachmentRoutingDecision(
        display_content=_resolve_content(display_blocks),
        llm_content=_resolve_content(llm_blocks),
        attachment_guidance=_attachment_policy(
            has_image_attachments=projection.image_total > 0,
            has_file_attachments=has_file_attachments,
            user_message=user_message,
        ),
        # 本轮以引用形式交给 read_image 的图片数（内联模式下已带 OCR 的不计）
        downgraded_image_count=projection.image_total
        - (projection.ocr_image_count if inlined else 0),
    )