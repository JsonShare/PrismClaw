"""文件字节 → 文本抽取。

分派规则：
- txt / md / csv / json / log  直接解码
- docx                         段落级解析：文字+图片交叉时保持顺序，图片走 OCR
- pdf                          先取文本层；文本层为空（扫描件）时渲染成图片走 OCR
- png / jpg / jpeg / bmp / tif 直接走 OCR

OCR 由调用方注入（``ocr(image_bytes, mime) -> str``），本模块不持有模型客户端，
因此是纯函数式的、可单测的：没有 OCR 也能验证分派与降级路径。
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from typing import Awaitable, Callable

# 文本层低于这个字符数就认为「抽不到东西」，转 OCR
PDF_TEXT_MIN_CHARS = 120
# 扫描件最多渲染这么多页，防止上百页 PDF 把 OCR 打爆
PDF_MAX_OCR_PAGES = 8
# 送进 LLM 的原文上限：超过就截断，避免大部头病历撑爆上下文
RAW_TEXT_LIMIT = 30_000

OcrFn = Callable[[bytes, str], Awaitable[str]]

logger = logging.getLogger(__name__)

_TEXT_EXT = {"txt", "md", "csv", "json", "log", "text", "html", "htm", "xml"}
_AUDIO_EXT = {"mp3", "wav", "m4a", "aac", "ogg", "flac", "wma", "mp4"}
_IMAGE_EXT = {"png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp"}
_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "bmp": "image/bmp",
    "gif": "image/gif",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "webp": "image/webp",
}


def _ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _collapse(text: str) -> str:
    """压掉多余空行与行尾空白，让原文在详情页里可读。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _looks_like_text(text: str) -> bool:
    """判断解码结果是不是人能读的文字。

    latin-1 永远不会抛异常，任何二进制都能「解」出一串符号——
    所以未知类型不能只看解没解出来，还得看可打印字符够不够多。
    """
    if not text:
        return False
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\t")
    return printable / len(text) >= 0.8


def _pdf_text_layer(data: bytes) -> str:
    """取 PDF 内嵌文本层。pypdf / pdfplumber 任一可用即可。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore[no-redef]
        except ImportError:
            return ""

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def _docx_text(data: bytes) -> tuple[str, str]:
    """读取 docx 段落文本。返回 (文本, 错误原因)。"""
    try:
        import docx
    except ImportError:
        return "", "缺少 python-docx，无法解析 docx"
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return "", f"docx 解析失败: {exc}"
    parts = [p.text for p in document.paragraphs if p.text and p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts), ""


def _natural_sort_key(s: str) -> list:
    """自然排序 key：image2.jpg < image10.jpg（数字部分按数值排）。"""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


def _docx_images(
    data: bytes, max_images: int = PDF_MAX_OCR_PAGES
) -> list[tuple[bytes, str]]:
    """从 docx 中提取内嵌图片（word/media/ 下的 JPEG/PNG）。

    返回 [(图片字节, MIME 类型)]，按自然排序保证 image2 在 image10 之前。
    限制提取数量以防上百张图撑爆 OCR。
    """
    import zipfile

    images: list[tuple[bytes, str]] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:  # noqa: BLE001
        return images
    for name in sorted(zf.namelist(), key=_natural_sort_key):
        if not name.startswith("word/media/"):
            continue
        low = name.lower()
        if low.endswith(".png"):
            mime = "image/png"
        elif low.endswith(".jpeg") or low.endswith(".jpg"):
            mime = "image/jpeg"
        else:
            continue
        try:
            images.append((zf.read(name), mime))
        except Exception:  # noqa: BLE001
            continue
        if len(images) >= max_images:
            break
    zf.close()
    return images


def _docx_parse(data: bytes) -> tuple[str, list[tuple[bytes, str]], str]:
    """段落级解析 docx：提取文字段落 + 图片段落，保持原始交叉顺序。

    返回 (文字内容, 图片列表[(bytes, mime)], 错误原因)。
    图片列表按 document.xml 中出现的段落顺序排列，
    保证「诊前图片→诊中文字→诊后图片」等交叉场景的顺序正确。

    实际 docx 结构（已验证 6 份质子报告）：
    - 图片不在 <w:p> 内与文字混排（MIXED 段落），而是整段 IMAGE
    - 典型模式：P003=1张封面图, P004=批量检查图片, P尾部=1张附录图
    - 图片通过 <a:blip r:embed="rIdN"> 引用 rels 中的 media 文件
    """
    import zipfile

    try:
        from lxml import etree
    except ImportError:
        # lxml 不可用时回退到纯文本+纯图片两段式
        text, err = _docx_text(data)
        if err:
            return "", [], err
        images = _docx_images(data)
        return text, images, ""

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return "", [], f"docx 解析失败: {exc}"

    # 关系映射：rId → media 路径
    rels: dict[str, str] = {}
    try:
        rels_xml = zf.read("word/_rels/document.xml.rels")
        rels_root = etree.fromstring(rels_xml)
        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        for rel in rels_root.iter(f"{{{rel_ns}}}Relationship"):
            rid = rel.get("Id", "")
            target = rel.get("Target", "")
            if rid and target:
                rels[rid] = target
    except Exception:  # noqa: BLE001
        pass

    # 解析 document.xml
    doc_xml = zf.read("word/document.xml")
    root = etree.fromstring(doc_xml)

    ns_w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns_a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    ns_r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    text_parts: list[str] = []
    image_parts: list[tuple[bytes, str]] = []

    for p in root.findall(f".//{{{ns_w}}}p"):
        # 提取段落文字
        texts = p.findall(f".//{{{ns_w}}}t")
        ptext = "".join(t.text or "" for t in texts).strip()

        # 提取段落内图片引用
        p_images: list[tuple[bytes, str]] = []
        for blip in p.findall(f".//{{{ns_a}}}blip"):
            embed = blip.get(f"{{{ns_r}}}embed", "")
            if embed not in rels:
                continue
            media_name = rels[embed]
            # 从 ZIP 中读取图片字节
            low = media_name.lower()
            if low.endswith(".png"):
                mime = "image/png"
            elif low.endswith(".jpeg") or low.endswith(".jpg"):
                mime = "image/jpeg"
            else:
                continue
            # media_name 是相对于 word/ 的路径，如 media/image1.jpg
            full_path = f"word/{media_name}" if not media_name.startswith("word/") else media_name
            try:
                img_bytes = zf.read(full_path)
                p_images.append((img_bytes, mime))
            except Exception:  # noqa: BLE001
                continue

        if ptext:
            text_parts.append(ptext)
        # 图片无论是否有伴随文字，都加入图片列表
        if p_images:
            image_parts.extend(p_images)

    # 表格文字
    for tbl in root.findall(f".//{{{ns_w}}}tbl"):
        for row in tbl.findall(f".//{{{ns_w}}}tr"):
            cells = []
            for tc in row.findall(f".//{{{ns_w}}}tc"):
                cell_texts = tc.findall(f".//{{{ns_w}}}t")
                cell_text = "".join(t.text or "" for t in cell_texts).strip()
                if cell_text:
                    cells.append(cell_text)
            if cells:
                text_parts.append(" | ".join(cells))

    zf.close()
    return "\n".join(text_parts), image_parts, ""


def _pdf_pages_as_images(data: bytes, max_pages: int) -> tuple[list[bytes], str]:
    """把扫描件 PDF 渲染成 PNG 字节列表。

    返回 (图片列表, 错误原因)。缺渲染依赖时原因非空。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return [], "扫描件 PDF 需要 PyMuPDF 才能渲染，当前环境未安装"

    images: list[bytes] = []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        for page in list(doc)[:max_pages]:
            pix = page.get_pixmap(dpi=150)
            images.append(pix.tobytes("png"))
        doc.close()
    except Exception as exc:  # noqa: BLE001
        return [], f"PDF 渲染失败: {exc}"
    if not images:
        return [], "PDF 没有可渲染的页面"
    return images, ""


async def extract_text(
    data: bytes,
    filename: str,
    *,
    ocr: OcrFn | None = None,
    max_ocr_pages: int = PDF_MAX_OCR_PAGES,
    ocr_sem: asyncio.Semaphore | None = None,
    on_page_done: Callable[[int, int], Awaitable[None]] | None = None,
) -> tuple[str, str]:
    """抽取文件文本。返回 (原文, 错误原因)，错误原因为空表示成功。

    扫描件（无文本层的 PDF / 图片）必须有 ocr 回调，否则返回明确的原因，
    由上层写成「解析失败」而不是留一条空原文的半成品记录。
    """
    if not data:
        return "", "文件为空"

    # OCR 路由：有 ocr_sem 走并行，否则走串行（向后兼容）
    async def _do_ocr(images: list[bytes], mime: str) -> tuple[str, str]:
        if ocr_sem is not None:
            return await _ocr_join_parallel(
                images, mime, ocr, ocr_sem=ocr_sem, on_page_done=on_page_done,
            )
        return await _ocr_join(images, mime, ocr)

    ext = _ext_of(filename)

    if ext in _TEXT_EXT:
        return _collapse(_decode(data))[:RAW_TEXT_LIMIT], ""

    if ext == "docx":
        text, images, err = _docx_parse(data)
        if err:
            return "", err
        collapsed = _collapse(text)

        # 场景 1：纯文字 docx — 直接返回
        if collapsed and not images:
            return collapsed[:RAW_TEXT_LIMIT], ""

        # 场景 2：纯图片 docx — OCR 降级
        if not collapsed and images:
            if ocr is None:
                return "", "docx 为扫描件（纯图片），未配置视觉模型无法识别"
            flat_imgs = [img for img, _mime in images]
            # 取第一张的 mime 作为统一 mime（同一 docx 内通常一致）
            mime = images[0][1]
            return await _do_ocr(flat_imgs, mime)

        # 场景 3：文字+图片交叉 docx — 文字保留，图片 OCR 后追加
        if collapsed and images:
            if ocr is None:
                # 没有视觉模型时只返回文字层，不报错——文字本身可用
                return collapsed[:RAW_TEXT_LIMIT], ""
            flat_imgs = [img for img, _mime in images]
            mime = images[0][1]
            ocr_text, ocr_err = await _do_ocr(flat_imgs, mime)
            if ocr_text:
                combined = f"{collapsed}\n\n--- 附件图片 OCR ---\n{ocr_text}"
                return _collapse(combined)[:RAW_TEXT_LIMIT], ""
            # OCR 失败时仍返回文字层
            return collapsed[:RAW_TEXT_LIMIT], ""

        # 场景 4：既无文字也无图片
        return "", "docx 没有可提取的文字"

    if ext == "pdf":
        layer = _collapse(_pdf_text_layer(data))
        if len(layer) >= PDF_TEXT_MIN_CHARS:
            return layer[:RAW_TEXT_LIMIT], ""
        # 文本层不够 → 扫描件，走 OCR
        if ocr is None:
            return layer, "未配置视觉模型，无法识别扫描件 PDF"
        images, err = _pdf_pages_as_images(data, max_ocr_pages)
        if err:
            return layer, err
        return await _do_ocr(images, "image/png")

    if ext in _IMAGE_EXT:
        if ocr is None:
            return "", "未配置视觉模型，无法识别图片"
        return await _do_ocr([data], _IMAGE_MIME.get(ext, "image/png"))

    if ext in _AUDIO_EXT:
        # 语音病历要走 ASR，不是 OCR；接进来之前先明确拒绝，
        # 别把 mp3 二进制当文本解出一堆乱码塞进原文
        return "", "语音病历暂不支持（需要接入 ASR）"

    # 未知类型：当纯文本试一次，确认是人能读的内容才认
    decoded = _collapse(_decode(data))
    if decoded and _looks_like_text(decoded):
        return decoded[:RAW_TEXT_LIMIT], ""
    return "", f"不支持的文件类型: {ext or '未知'}"


async def _ocr_join_parallel(
    images: list[bytes],
    mime: str,
    ocr: OcrFn,
    *,
    ocr_sem: asyncio.Semaphore,
    on_page_done: Callable[[int, int], Awaitable[None]] | None = None,
) -> tuple[str, str]:
    """并行 OCR 后拼接。

    与 _ocr_join 的行为差异：
    - 并行执行，总耗时 ≈ max(单页) 而非 sum(全部)
    - 任一页失败不中止其余页（P10 修复）
    - 页级进度回调（P9 修复）
    - ocr_sem 必须由调用方传入，保证同一信号量在所有协程间共享
    """
    total = len(images)
    results: list[tuple[int, str]] = []   # (page_index, text)
    errors: list[tuple[int, str]] = []    # (page_index, error)

    async def _ocr_one(idx: int, img: bytes) -> None:
        async with ocr_sem:                          # 共享信号量，在外层创建一次
            try:
                page_text = await ocr(img, mime)
                if page_text:
                    results.append((idx, page_text.strip()))
            except Exception as exc:  # noqa: BLE001
                errors.append((idx, f"第 {idx + 1} 页 OCR 失败: {exc}"))
            finally:
                if on_page_done:
                    await on_page_done(idx + 1, total)

    await asyncio.gather(*[_ocr_one(i, img) for i, img in enumerate(images)])

    # 按页码排序拼接（保持原始顺序）
    results.sort(key=lambda r: r[0])
    multipage = total > 1
    parts: list[str] = []
    for idx, text in results:
        prefix = f"— 第 {idx + 1} 页 —\n" if multipage else ""
        parts.append(prefix + text)

    joined = _collapse("\n".join(parts))
    if not joined:
        if errors:
            return "", errors[0][1]
        return "", "OCR 未识别出文字"

    if errors:
        logger.warning("OCR 部分页失败: %s", "; ".join(e for _, e in errors))

    return joined[:RAW_TEXT_LIMIT], ""


async def _ocr_join(images: list[bytes], mime: str, ocr: OcrFn) -> tuple[str, str]:
    """逐页 OCR 后拼接。任一页抛错即中止并回报原因。

    单页不加页眉——「— 第 1 页 —」对一张图来说纯属噪音。
    """
    multipage = len(images) > 1
    parts: list[str] = []
    for idx, img in enumerate(images):
        try:
            page_text = await ocr(img, mime)
        except Exception as exc:  # noqa: BLE001
            if not parts:
                return "", f"OCR 失败: {exc}"
            return _collapse("\n".join(parts))[:RAW_TEXT_LIMIT], ""
        if page_text:
            prefix = f"— 第 {idx + 1} 页 —\n" if multipage else ""
            parts.append(prefix + page_text.strip())
    joined = _collapse("\n".join(parts))
    if not joined:
        return "", "OCR 未识别出文字"
    return joined[:RAW_TEXT_LIMIT], ""
