"""按页解析文档为块，并以会话级缓存支撑惰性读取。

一个文档被打开后（``open_doc``），可按页解析（``parse_page``），解析结果以
单页 JSON 落盘；再次读取同一页直接命中缓存，不重新解析。引用坐标由
``Cursor`` 给出，形如 ``doc:<hash8>/p3/b7@text``。

PDF 由 pdfium 提供文本层与整页渲染：``parse_page`` 只取文本层；需要 OCR 的
扫描页由调用方用 ``render_page_images`` 在锁内渲染成 JPEG，再到锁外调视觉模型。

分页语义：pdf 按页，odp 按幻灯片，epub 按章节，ofd 按页；docx/markdown/rtf/
odt/ods/html/mhtml/csv/xls 无页概念，恒为第 1 页。这些「静态格式」由解析器
一次性转成块（``DocHandle``），不持有原生句柄。

模块同时承担图片落盘与模型路径映射（``to_model_path``）：图片只写路径不写
base64，模型经 ``read`` 读取。
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from importlib.metadata import version as _pkg_version
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeGuard

import pypdfium2 as pdfium

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from docx.text.paragraph import Paragraph

    class _PilImage(Protocol):
        """pdfium 位图转出的 Pillow 图像中本文用到的方法。"""

        mode: str

        def convert(self, mode: str) -> _PilImage: ...

        def save(self, fp: io.BytesIO, *, format: str, quality: int = ...) -> None: ...

    class _PdfBitmap(Protocol):
        def to_pil(self) -> _PilImage: ...

    class _PdfObject(Protocol):
        @property
        def type(self) -> int: ...

        def get_bounds(self) -> tuple[float, float, float, float]: ...

    class _PdfTextPage(Protocol):
        def get_text_bounded(self) -> str | None: ...

    class _PdfPage(Protocol):
        def get_textpage(self) -> _PdfTextPage: ...

        def get_size(self) -> tuple[float, float]: ...

        def get_objects(self) -> Iterator[_PdfObject]: ...

        def render(self, *, scale: float) -> _PdfBitmap: ...

    class _PdfDocument(Protocol):
        """pdfium 原生文档句柄（该库无类型标注，按实际调用的成员声明）。"""

        def __len__(self) -> int: ...

        def __getitem__(self, index: int) -> _PdfPage: ...

        def close(self) -> None: ...

    class _XmlNode(Protocol):
        """docx 正文 XML 节点（lxml 元素）中本文用到的成员。"""

        tag: str
        attrib: dict[str, str]

        def iter(self, tag: str | None = None) -> Iterator[_XmlNode]: ...

        def getparent(self) -> _XmlNode | None: ...

        def get(self, key: str, default: str | None = None) -> str | None: ...

        def __iter__(self) -> Iterator[_XmlNode]: ...

    type DocLike = _PdfDocument | DocxDocument | DocHandle | str

# pdfium 原生库不支持多线程：并发调用 get_textpage / render 会 SIGABRT 或返回
# "Data format error"，仅 open/len 安全。所有 pdfium 调用经此锁串行化。
# 代价是 PDF 解析无法并行；单页耗时 1.4–2.0ms，实测不构成瓶颈。
_PDFIUM_LOCK = threading.Lock()

# 同一页可能被并发解析（附件 4 线程预览同一文件）。页文件与其登记在 meta 里的
# 摘要必须成对落盘：分开加锁会出现「文件内容是 A、摘要是 B」的错配，读取侧一律
# 判为篡改而丢弃缓存。故这两步共用一把锁。锁内不做 pdfium 调用，无嵌套风险。
_CACHE_LOCK = threading.Lock()

# 支持的文档格式：扩展名 → 解析键。全仓唯一真源。
# 分页语义：pdf 按页；odp 按幻灯片；其余无页概念（恒第 1 页）。
DOC_FORMATS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "markdown",
    ".rtf": "rtf",
    ".odt": "odt",
    ".ods": "ods",
    ".odp": "odp",
    ".epub": "epub",
    ".ofd": "ofd",
    ".html": "html",
    ".htm": "html",
    ".mhtml": "mhtml",
    ".mht": "mhtml",
    ".csv": "csv",
    ".tsv": "csv",
    ".xls": "xls",
}

# 结构化 Office（.xlsx/.pptx）不在 DOC_FORMATS 内：它们走 read_office_text 的
# 行式锚点输出（s#S!r#Rc#C / slide#N/tf#M），编辑工具依赖该锚点定位。
# 内嵌图片由 :func:`structured_office_images` 单独提取，见 read 分支。
STRUCTURED_OFFICE_FORMATS: dict[str, str] = {
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}

# 无页概念格式：只接受 page==1，vision 档无意义。
# epub 按章节、odp 按幻灯片、ofd 按页，都有真实页号，不在此列。
PAGELESS_FORMATS: frozenset[str] = frozenset(
    {"docx", "markdown", "rtf", "odt", "ods", "html", "mhtml", "csv", "xls"}
)

# 缓存结构版本：块模型或落盘格式变化时递增，旧缓存自动作废。
SCHEMA_VERSION = 2

# 单次返回的字符上限。超出时在自然边界截断并给出 /c 游标续读。
# 非运营旋钮：取值使一屏约 3k token，与 read 的默认分页量级一致。
_WINDOW_CHAR_BUDGET = 6000

# OCR 转录文本少于该长度时保留页面图像：图表页/封面的版面会随转录丢失，
# 模型可二次 read 看原图。与「有没有文本层」的判据不同量级。
_OCR_MIN_TEXT_FOR_IMAGE_DROP = 200

# 扫描页判定：图像包围盒面积占比超过该阈值，且文本层字符数低于另一阈值。
# 面积占比必须先 clamp（实测多图重叠时原始比值可达 1.39–2.0）。
_SCANNED_RATIO_THRESHOLD = 0.8
_SCANNED_CHAR_THRESHOLD = 40

# 无文本层页的缓存文案保持中性：是否「可用 vision 识别」随 OCR 尝试状态变化，
# 由渲染层按需追加指引，避免把一次性判断固化进缓存造成死循环。
_SCANNED_REASON = "该页无可提取文本层，可能为扫描件。"

# 永久性失败的文案前缀：页面结构损坏、渲染失败。这两类不可恢复，渲染层据此
# 不给「可重试」指引。用常量而非散落字符串，避免文案改动后判定失效。
PERMANENT_FAILURE_PREFIXES = ("该页无法加载", "该页渲染失败", "该页内容缺失")

TIERS = ("text", "vision")

# 图片扩展名 → mime。文档内嵌图与附件图片共用一张表，避免两处各写一份。
IMAGE_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

_SESSION_VISIBLE_DIRS = frozenset({"inbox", "outbox", "context_blocks", "tool_results"})


# ---------------------------------------------------------------------------
# 块模型
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Block:
    """页内一个可引用单元。

    ``index`` 是页内序号（1-based），与 ``page`` 一起构成稳定定位符。
    ``is_ocr`` 标记这段文字来自视觉模型转录而非文本层：用户与模型都必须知道
    它可能有识别错误，渲染时加 ``[OCR]`` 前缀。
    """

    kind: Literal["text", "image", "table", "unsupported"]
    page: int
    index: int
    text: str = ""
    rows: tuple[tuple[str, ...], ...] = ()
    source_path: str | None = None
    mime_type: str | None = None
    is_ocr: bool = False


def _block_to_dict(block: Block) -> dict[str, Any]:
    return {
        "kind": block.kind,
        "page": block.page,
        "index": block.index,
        "text": block.text,
        "rows": [list(row) for row in block.rows],
        "source_path": block.source_path,
        "mime_type": block.mime_type,
        "is_ocr": block.is_ocr,
    }


def _block_from_dict(data: dict[str, Any]) -> Block:
    return Block(
        kind=data["kind"],
        page=int(data["page"]),
        index=int(data["index"]),
        text=data.get("text") or "",
        rows=tuple(tuple(cell for cell in row) for row in data.get("rows") or ()),
        source_path=data.get("source_path"),
        mime_type=data.get("mime_type"),
        is_ocr=bool(data.get("is_ocr")),
    )


# ---------------------------------------------------------------------------
# 路径映射
# ---------------------------------------------------------------------------
def to_model_path(rel_path: str) -> str:
    """物理工作区相对路径 → 模型可解析的虚拟前缀。

    ``sessions/<sid>/{inbox,outbox,context_blocks,tool_results}/...`` 折叠为
    虚拟前缀；``.work/...`` 系统区挂在 ``project/`` 下（只读可解析）。
    """
    parts = Path(rel_path).parts
    if len(parts) >= 4 and parts[0] == "sessions" and parts[2] in _SESSION_VISIBLE_DIRS:
        return Path(*parts[2:]).as_posix()
    if parts and parts[0] == ".work":
        return Path("project", *parts).as_posix()
    return rel_path


def _session_root_of(workspace_path: Path, source_rel_path: str) -> Path | None:
    parts = Path(source_rel_path).parts
    if len(parts) >= 3 and parts[0] == "sessions":
        return workspace_path / "sessions" / parts[1]
    return None


def _save_image(
    workspace_path: Path,
    source_rel_path: str,
    counter: int,
    ext: str,
    data: bytes,
) -> str:
    """落盘一张内嵌图，返回工作区相对物理路径。

    会话内文件写 ``sessions/<sid>/context_blocks/images/<key>/``；工作区根文件
    写 ``<ws>/.work/cache/extracted/<key>/``（经 :func:`to_model_path` 映射为
    ``project/.work/...``，仍可被 read 解析）。内容未变时跳过重写，保持 OCR
    缓存的 stat 指纹有效。
    """
    src = Path(source_rel_path)
    abs_src = (workspace_path / src).resolve(strict=False)
    source_key = hashlib.sha1(str(abs_src).encode("utf-8")).hexdigest()[:16]
    session_root = _session_root_of(workspace_path, source_rel_path)
    if session_root is not None:
        out_dir = session_root / "context_blocks" / "images" / source_key
    else:
        out_dir = workspace_path / ".work" / "cache" / "extracted" / source_key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{src.stem}_img_{counter:03d}{ext}"
    try:
        if out_path.read_bytes() == data:
            return out_path.relative_to(workspace_path).as_posix()
    except OSError:
        pass
    out_path.write_bytes(data)
    return out_path.relative_to(workspace_path).as_posix()


# 二进制抽取出的正文常带排版专用字符：连字符 U+2010、数字范围 U+2013，模型
# 按 ASCII '-' 检索会匹配不到。这里折叠为 ASCII，并清掉零宽字符与不换行空格。
# 刻意不折叠 U+2014（中文破折号「——」语义重要），也不做 NFKC（会改全角括号）。
_TYPOGRAPHIC_TRANSLATION = {
    0x2010: "-",
    0x2011: "-",
    0x2012: "-",
    0x2013: "-",
    0x2212: "-",
    0xFF0D: "-",
    0x00A0: " ",
    0x200B: "",
    0x200C: "",
    0x200D: "",
    0xFEFF: "",
}


def _normalize(text: str) -> str:
    return text.translate(_TYPOGRAPHIC_TRANSLATION)


# ---------------------------------------------------------------------------
# 打开与页数
# ---------------------------------------------------------------------------
def open_doc(
    raw: bytes,
    fmt: str,
    *,
    workspace_path: Path | None = None,
    source_rel_path: str = "",
) -> DocLike:
    """打开文档，返回解析句柄。

    ``DocHandle`` 是新增格式的统一载体（打开即解析完），pdf/docx/markdown 维持
    各自的原生句柄以复用既有的惰性解析与页模型。``workspace_path`` 仅供需要
    落盘内嵌资源（如 OFD 页面图）的格式使用。
    """
    if fmt == "pdf":
        with _PDFIUM_LOCK:
            try:
                pdf_doc: _PdfDocument = pdfium.PdfDocument(raw)
            except pdfium.PdfiumError as exc:
                # 加密文件在 pdfium 层是 FPDF_ERR_PASSWORD，原始消息不可读，
                # 这里转成可操作文案（方案 §8.2 承诺不把底层错误抛给模型）。
                if "password" in str(exc).lower():
                    raise ValueError("PDF 已加密，需要密码才能读取") from exc
                raise ValueError(f"PDF 无法打开：{exc}") from exc
            return pdf_doc
    if fmt == "docx":
        import docx

        return docx.Document(io.BytesIO(raw))
    if fmt == "markdown":
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")

    parser = _STATIC_PARSERS.get(fmt)
    if parser is not None:
        try:
            pages = parser(raw, workspace_path=workspace_path, source_rel_path=source_rel_path)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 - 第三方库异常统一转可读文案
            raise ValueError(
                f"{fmt.upper()} 文件无法解析，可能已损坏或不是真正的 {fmt.upper()} 文件"
            ) from exc
        return DocHandle(pages=pages)
    raise ValueError(f"不支持的文档格式：{fmt}")


def page_count(doc: DocLike, fmt: str) -> int:
    if fmt == "pdf" and _is_pdf(doc):
        with _PDFIUM_LOCK:
            return len(doc)
    if isinstance(doc, DocHandle):
        return len(doc.pages)
    return 1


def close_doc(doc: DocLike, fmt: str) -> None:
    if fmt == "pdf" and _is_pdf(doc):
        with _PDFIUM_LOCK, contextlib.suppress(Exception):
            doc.close()


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def parse_page(
    doc: DocLike,
    page: int,
    *,
    workspace_path: Path | None = None,
    source_rel_path: str = "",
) -> list[Block]:
    """解析单页为块列表（``page`` 1-based）。

    只取文本层，不渲染、不外发。扫描页需要 OCR 时由调用方用
    :func:`render_page_images` 拿到页面图像，再到锁外调视觉模型。
    无页概念格式只接受 ``page == 1``。
    """
    if page < 1:
        raise ValueError("页码从 1 开始")
    if doc is None:
        raise ValueError("文档句柄为空")

    if _is_pdf(doc):
        if page > len(doc):
            raise ValueError(f"页码 {page} 超出范围（共 {len(doc)} 页）")
        return _parse_pdf_page(
            doc, page, workspace_path=workspace_path, source_rel_path=source_rel_path
        )

    if isinstance(doc, DocHandle):
        if page > len(doc.pages):
            raise ValueError(f"页码 {page} 超出范围（共 {len(doc.pages)} 页）")
        return list(doc.pages[page - 1])

    if page > 1:
        raise ValueError("该格式无页概念，只支持第 1 页")

    if _is_docx(doc):
        return _parse_docx(doc, workspace_path=workspace_path, source_rel_path=source_rel_path)
    if isinstance(doc, str):
        return _parse_markdown(doc, workspace_path=workspace_path, source_rel_path=source_rel_path)
    raise ValueError("无法识别的文档句柄类型")


def _is_pdf(doc: object) -> TypeGuard[_PdfDocument]:
    return isinstance(doc, pdfium.PdfDocument)


def _is_docx(doc: object) -> TypeGuard[DocxDocument]:
    return hasattr(doc, "paragraphs") and hasattr(doc, "tables")


def _parse_pdf_page(
    doc: _PdfDocument,
    page: int,
    *,
    workspace_path: Path | None,
    source_rel_path: str,
) -> list[Block]:
    return parse_pdf_page_with_modality(
        doc,
        page,
        workspace_path=workspace_path,
        source_rel_path=source_rel_path,
    )[0]


def parse_pdf_page_with_modality(
    doc: DocLike,
    page: int,
    *,
    workspace_path: Path | None = None,
    source_rel_path: str = "",
) -> tuple[list[Block], dict[str, Any]]:
    """解析一页并顺带返回该页的模态信息（供 ``page_modality`` 累积）。

    模态只在解析该页时计算一次，属惰性探测：不做全量预扫（实测 240 页预扫
    1093ms，惰性仅 13ms）。
    """
    if not _is_pdf(doc):
        raise ValueError("该格式不是 PDF，无法按 PDF 页解析")
    with _PDFIUM_LOCK:
        try:
            pdf_page = doc[page - 1]
        except Exception as exc:  # noqa: BLE001 - 单页损坏不应让整份文档不可读
            return (
                [
                    Block(
                        kind="unsupported",
                        page=page,
                        index=1,
                        text=(f"该页无法加载（{type(exc).__name__}），PDF 中此页结构可能损坏"),
                    )
                ],
                {"chars": 0, "ratio": 0.0, "scanned": False},
            )
        blocks = _parse_pdf_page_locked(
            pdf_page,
            page,
            workspace_path=workspace_path,
            source_rel_path=source_rel_path,
        )
        modality = _page_modality_of(pdf_page, blocks)
    return blocks, modality


def _page_modality_of(pdf_page: _PdfPage, blocks: list[Block]) -> dict[str, Any]:
    """在已持锁的前提下统计该页的 ``r`` 与文本字符数。"""
    chars = sum(len(block.text) for block in blocks if block.kind == "text")
    ratio = page_image_ratio(pdf_page)
    scanned = ratio > _SCANNED_RATIO_THRESHOLD and chars < _SCANNED_CHAR_THRESHOLD
    return {"chars": chars, "ratio": round(ratio, 3), "scanned": scanned}


def _parse_pdf_page_locked(
    pdf_page: _PdfPage,
    page: int,
    *,
    workspace_path: Path | None,
    source_rel_path: str,
) -> list[Block]:
    raw_text = pdf_page.get_textpage().get_text_bounded() or ""
    text = _normalize(raw_text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not text:
        return [Block(kind="unsupported", page=page, index=1, text=_SCANNED_REASON)]
    # PDF 无可靠段落边界（版面理解已被排除），按空行切分；无空行时整页一块。
    segments = [seg.strip() for seg in re.split(r"\n\s*\n", text) if seg.strip()]
    return [
        Block(kind="text", page=page, index=i, text=seg) for i, seg in enumerate(segments, start=1)
    ]


def page_image_ratio(pdf_page: _PdfPage) -> float:
    """图像包围盒总面积 / 页面面积，clamp 到 [0, 1]。

    pdfium 的页面对象坐标以左下角为原点、单位为 pt；多图重叠时未 clamp 的
    比值会超过 1。这里只做几何统计，不触发渲染。
    """
    try:
        width, height = pdf_page.get_size()
        if width <= 0 or height <= 0:
            return 0.0
        area = 0.0
        for obj in pdf_page.get_objects():
            if obj.type != pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                continue
            x0, y0, x1, y1 = obj.get_bounds()
            area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
        return min(1.0, max(0.0, area / (width * height)))
    except Exception:  # noqa: BLE001 - 几何统计失败按「无图」处理，不影响文本读取
        return 0.0


# ---------------------------------------------------------------------------
# 页面渲染（锁内，仅 pdfium + 编码；不做任何网络 IO）
# ---------------------------------------------------------------------------
def render_page_images(
    doc: DocLike,
    pages: Sequence[int],
    *,
    dpi: int = 150,
) -> tuple[dict[int, bytes], dict[int, str]]:
    """把指定页渲染为 JPEG 字节，返回 ``(images, failures)``。

    ``failures`` 是 ``页号 → 错误说明``：单页损坏只影响该页。

    整个批次在同一把 ``_PDFIUM_LOCK`` 内完成。pdfium 原生库不支持多线程，
    锁外调用会 SIGABRT；因此**锁内禁止**调用 ``open_doc`` / ``page_count`` /
    ``close_doc``（它们各自取锁，非可重入会自锁），也禁止任何网络与文件写入。
    """
    images: dict[int, bytes] = {}
    failures: dict[int, str] = {}
    if not _is_pdf(doc):
        raise ValueError("该格式不是 PDF，无法渲染页面")
    with _PDFIUM_LOCK:
        for page in pages:
            try:
                pdf_page = doc[page - 1]
                bitmap = pdf_page.render(scale=dpi / 72)
                buf = io.BytesIO()
                bitmap.to_pil().convert("RGB").save(buf, format="JPEG", quality=85)
                images[page] = buf.getvalue()
            except Exception as exc:  # noqa: BLE001 - 单页渲染失败不影响其它页
                failures[page] = f"该页渲染失败（{type(exc).__name__}），可能页面结构损坏"
    return images, failures


def render_page_image(doc: DocLike, page: int, *, dpi: int = 150) -> bytes:
    """渲染单页为 JPEG 字节；失败抛 ``ValueError``（消息可直接给模型）。"""
    images, failures = render_page_images(doc, (page,), dpi=dpi)
    if page in failures:
        raise ValueError(failures[page])
    return images[page]


def build_ocr_page_blocks(
    page: int,
    text: str,
    jpeg: bytes,
    *,
    workspace_path: Path | None,
    source_rel_path: str,
) -> list[Block]:
    """把一页的 OCR 转录组装成块。

    文本充足（``_OCR_MIN_TEXT_FOR_IMAGE_DROP`` 及以上）时只产出文本块；文本过短
    说明该页以图表/版面为主，转录会丢版面，故额外落盘页面图像并附 image 块，
    模型可二次 ``read`` 看原图。
    """
    body = text.strip()
    blocks = [Block(kind="text", page=page, index=1, text=body, is_ocr=True)]
    if workspace_path is not None and len(body) < _OCR_MIN_TEXT_FOR_IMAGE_DROP:
        rel = _save_image(workspace_path, source_rel_path, page, ".jpg", jpeg)
        blocks.append(
            Block(
                kind="image",
                page=page,
                index=2,
                source_path=rel,
                mime_type="image/jpeg",
            )
        )
    return blocks


# ---- docx -----------------------------------------------------------------
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_HEADING_STYLE_RE = re.compile(r"(?:Heading|heading|标题)\s*(\d)")


def _parse_docx(
    doc: DocxDocument,
    *,
    workspace_path: Path | None,
    source_rel_path: str,
) -> list[Block]:
    """按 OOXML 顺序产出段落、表格与内嵌图片块。"""
    blocks: list[Block] = []
    index = 0

    def next_index() -> int:
        nonlocal index
        index += 1
        return index

    # 段落与表格的文档顺序：python-docx 的 paragraphs / tables 是分开的列表，
    # 需按 body 子元素顺序遍历。图片走 zip 内 rel 映射。
    image_counter = 0
    media: dict[str, bytes] = {}
    rel_map: dict[str, str] = {}
    if workspace_path is not None:
        rel_map, media = _docx_media(doc)

    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            text = _paragraph_text(para)
            if text:
                blocks.append(Block(kind="text", page=1, index=next_index(), text=text))
            if workspace_path is not None:
                for rid, degrees in _paragraph_images(child):
                    target = rel_map.get(rid)
                    if not target or target not in media:
                        continue
                    image_counter += 1
                    ext = Path(target).suffix.lower() or ".png"
                    rel = _save_image(
                        workspace_path,
                        source_rel_path,
                        image_counter,
                        ext,
                        _orient_image(media[target], degrees),
                    )
                    blocks.append(
                        Block(
                            kind="image",
                            page=1,
                            index=next_index(),
                            source_path=rel,
                            mime_type=IMAGE_MIME_BY_EXT.get(ext, "image/png"),
                        )
                    )
        elif tag == "tbl":
            table = Table(child, doc)
            rows = tuple(tuple(cell.text.strip() for cell in row.cells) for row in table.rows)
            blocks.append(Block(kind="table", page=1, index=next_index(), rows=rows))

    return blocks


def _paragraph_text(para: Paragraph) -> str:
    text = para.text.strip()
    if not text:
        return ""
    style = ""
    try:
        style = para.style.name if para.style is not None else ""
    except Exception:  # noqa: BLE001 - 缺失样式定义时按正文处理
        style = ""
    match = _HEADING_STYLE_RE.search(style or "")
    if match:
        text = f"{'#' * int(match.group(1))} {text}"
    para_id = _paragraph_para_id(para)
    if para_id:
        return f"[{para_id}] {text}"
    return text


def _paragraph_para_id(para: Paragraph) -> str | None:
    """取 ``w14:paraId``，供 docx 编辑工具按段落定位。"""
    for key, value in para._p.attrib.items():
        if key.rsplit("}", 1)[-1] == "paraId":
            return value
    return None


def _paragraph_images(node: _XmlNode) -> list[tuple[str, int]]:
    """按文档顺序返回段落内图片的 (rId, 声明旋转角度)。"""
    images: list[tuple[str, int]] = []
    for elem in node.iter():
        if elem.tag.rsplit("}", 1)[-1] != "blip":
            continue
        rid = next(
            (v for k, v in elem.attrib.items() if k.rsplit("}", 1)[-1] == "embed"),
            None,
        )
        if rid:
            images.append((rid, _docx_image_rotation(elem)))
    return images


def _docx_image_rotation(node: _XmlNode) -> int:
    """取图片声明的显示旋转角度（度，顺时针），无声明返回 0。

    关键：``word/media/`` 里存的是**未旋转位图**，旋转只写在
    ``pic:spPr/a:xfrm/@rot``（单位 1/60000 度）。xfrm 是 ``blip`` 的兄弟分支
    （都在 ``pic:pic`` 下），因此必须上溯到 ``pic`` 再向下找 ``spPr``；
    顺着 ``blip`` 的祖先链向上永远找不到。

    真实故障：用户上传的 docx 里 3 页扫描件声明 ``rot=16200000``（270°），
    原实现直接落盘原始字节，视觉模型看到的是文字倒转 90° 的页面，只能猜数值
    （同一页多次请求出现 8.8/11.16/13.87 三个不同 WBC），并把 max_tokens 耗尽。
    """
    cur: _XmlNode = node
    pic: _XmlNode | None = None
    for _ in range(10):
        parent = cur.getparent()
        if parent is None:
            break
        cur = parent
        if cur.tag.rsplit("}", 1)[-1] == "pic":
            pic = cur
            break
    if pic is None:
        return 0
    for child in pic:
        if child.tag.rsplit("}", 1)[-1] != "spPr":
            continue
        for sub in child:
            if sub.tag.rsplit("}", 1)[-1] == "xfrm":
                raw = sub.get("rot")
                if raw:
                    return round(int(raw) / 60000) % 360
    return 0


def _orient_image(data: bytes, degrees: int) -> bytes:
    """按声明角度旋转图片位图（顺时针）。无法解码时返回原字节。"""
    if degrees % 360 == 0:
        return data
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        # Pillow 的 rotate 是逆时针，故取负值。
        rotated = img.rotate(-degrees, expand=True)
        buf = io.BytesIO()
        if img.format == "JPEG" and rotated.mode not in ("RGB", "L"):
            rotated = rotated.convert("RGB")
        rotated.save(buf, format=img.format or "PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - 图片损坏/格式不支持时保持原样
        return data


def _docx_media(doc: DocxDocument) -> tuple[dict[str, str], dict[str, bytes]]:
    """从 docx 包内读关系表与媒体文件。"""
    try:
        part = doc.part
        rel_map: dict[str, str] = {}
        media: dict[str, bytes] = {}
        for rid, rel in part.rels.items():
            target = getattr(rel, "target_part", None)
            if target is None:
                continue
            partname = str(target.partname).lstrip("/")
            if not partname.startswith("word/media/"):
                continue
            rel_map[rid] = partname
            media[partname] = target.blob
        return rel_map, media
    except Exception:  # noqa: BLE001 - 关系表异常时退化为纯文本解析
        return {}, {}


# ---- 结构化 Office（xlsx/pptx）内嵌图片 ------------------------------------
# 两类包都是 OOXML，图片一律存未旋转位图，旋转只写在 ``a:xfrm/@rot``
# （1/60000 度），与 docx 完全一致（已实测：pptx ``p:pic``、xlsx ``xdr:pic``）。
def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _zip_rels(zf: zipfile.ZipFile, part_path: str) -> dict[str, tuple[str, str]]:
    """读部件的关系表，返回 ``rId → (包内成员名, 关系类型)``。

    相对 Target 以部件所在目录为基准解析；以 ``/`` 开头的是包内绝对路径。
    """
    part = PurePosixPath(part_path)
    rels_path = (part.parent / "_rels" / f"{part.name}.rels").as_posix()
    try:
        root = ET.fromstring(zf.read(rels_path))
    except (KeyError, ET.ParseError):
        return {}
    out: dict[str, tuple[str, str]] = {}
    for rel in root:
        if _local_name(rel.tag) != "Relationship":
            continue
        rid, target = rel.get("Id"), (rel.get("Target") or "").strip()
        if not rid or not target:
            continue
        member = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join(part.parent.as_posix(), target))
        )
        out[rid] = (member, rel.get("Type") or "")
    return out


def _rel_id(node: ET.Element) -> str | None:
    """取 ``r:id`` 属性值。

    必须区分 ``p:sldId`` 上的整数 ``id`` 与关系 ``r:id``：两者本地名都是
    ``id``，只有后者带命名空间前缀。取带命名空间的那个。
    """
    for key, value in node.attrib.items():
        if key.startswith("{") and _local_name(key) == "id":
            return value
    return None


def _pic_rid_and_rotation(pic: ET.Element) -> tuple[str, int] | None:
    """从 ``p:pic`` / ``xdr:pic`` 取 (rId, 声明旋转角度)。

    旋转写在 ``pic > spPr > a:xfrm/@rot``；只沿直接子节点找 ``spPr``，
    与 docx 的实现保持同一判据。
    """
    rid = None
    for elem in pic.iter():
        if _local_name(elem.tag) == "blip":
            rid = next((v for k, v in elem.attrib.items() if _local_name(k) == "embed"), None)
            break
    if not rid:
        return None
    degrees = 0
    for child in pic:
        if _local_name(child.tag) != "spPr":
            continue
        for sub in child:
            if _local_name(sub.tag) != "xfrm":
                continue
            raw = sub.get("rot")
            if raw:
                degrees = round(int(raw) / 60000) % 360
    return rid, degrees


def _pic_sources(
    zf: zipfile.ZipFile, names: set[str], root: ET.Element, rels: dict[str, tuple[str, str]]
) -> list[tuple[str, int]]:
    """按文档顺序返回 ``(包内成员名, 旋转角度)``，跳过缺关系或缺失的部件。"""
    out: list[tuple[str, int]] = []
    for node in root.iter():
        if _local_name(node.tag) != "pic":
            continue
        found = _pic_rid_and_rotation(node)
        if found is None:
            continue
        target = rels.get(found[0])
        if target is None or target[0] not in names:
            continue
        out.append((target[0], found[1]))
    return out


def _pptx_pictures(zf: zipfile.ZipFile, names: set[str]) -> list[tuple[int, str, int]]:
    """按幻灯片顺序返回 ``(幻灯片号, 媒体成员名, 旋转角度)``。"""
    part = "ppt/presentation.xml"
    if part not in names:
        return []
    root = ET.fromstring(zf.read(part))
    rels = _zip_rels(zf, part)
    slides: list[str] = []
    for node in root.iter():
        if _local_name(node.tag) != "sldId":
            continue
        rid = _rel_id(node)
        if rid and rid in rels:
            slides.append(rels[rid][0])

    found: list[tuple[int, str, int]] = []
    for slide_no, slide_path in enumerate(slides, start=1):
        if slide_path not in names:
            continue
        slide_root = ET.fromstring(zf.read(slide_path))
        for member, degrees in _pic_sources(zf, names, slide_root, _zip_rels(zf, slide_path)):
            found.append((slide_no, member, degrees))
    return found


def _xlsx_pictures(zf: zipfile.ZipFile, names: set[str]) -> list[tuple[int, str, int]]:
    """按工作表顺序返回内嵌图（经 sheet → drawing → media 三层关系）。"""
    part = "xl/workbook.xml"
    if part not in names:
        return []
    root = ET.fromstring(zf.read(part))
    rels = _zip_rels(zf, part)
    sheets: list[str] = []
    for node in root.iter():
        if _local_name(node.tag) != "sheet":
            continue
        rid = _rel_id(node)
        if rid and rid in rels:
            sheets.append(rels[rid][0])

    found: list[tuple[int, str, int]] = []
    for sheet_no, sheet_path in enumerate(sheets, start=1):
        if sheet_path not in names:
            continue
        for member, rel_type in _zip_rels(zf, sheet_path).values():
            if not rel_type.endswith("/drawing") or member not in names:
                continue
            drawing_root = ET.fromstring(zf.read(member))
            for media, degrees in _pic_sources(zf, names, drawing_root, _zip_rels(zf, member)):
                found.append((sheet_no, media, degrees))
    return found


def structured_office_images(
    raw: bytes,
    fmt: str,
    *,
    workspace_path: Path,
    source_rel_path: str = "",
) -> list[Block]:
    """提取 .pptx/.xlsx 内嵌图片为 image 块，落盘前按声明旋转角还原。

    ``page`` 是幻灯片号 / 工作表号，``index`` 是全文图片序号（1-based），
    与 docx 的块口径一致，模型可据此 `read` 单张图走视觉识别。
    """
    if fmt not in STRUCTURED_OFFICE_FORMATS.values():
        raise ValueError(f"structured_office_images 不支持格式：{fmt}")

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())
            found = _pptx_pictures(zf, names) if fmt == "pptx" else _xlsx_pictures(zf, names)
            blocks: list[Block] = []
            for page_no, member, degrees in found:
                counter = len(blocks) + 1
                ext = PurePosixPath(member).suffix.lower() or ".png"
                source = _save_image(
                    workspace_path,
                    source_rel_path,
                    counter,
                    ext,
                    _orient_image(zf.read(member), degrees),
                )
                blocks.append(
                    Block(
                        kind="image",
                        page=page_no,
                        index=counter,
                        source_path=source,
                        mime_type=IMAGE_MIME_BY_EXT.get(ext, "image/png"),
                    )
                )
    except (zipfile.BadZipFile, ET.ParseError, KeyError):
        return []
    return blocks


# ---- markdown -------------------------------------------------------------
def _parse_markdown(
    text: str,
    *,
    workspace_path: Path | None,
    source_rel_path: str,
) -> list[Block]:
    text = _normalize(text)
    blocks: list[Block] = []
    index = 0
    src = Path(source_rel_path)
    if workspace_path is not None:
        ws_root = workspace_path.resolve()
        base_dir = (workspace_path / src.parent).resolve() if src.parent != Path(".") else ws_root
    else:
        base_dir = ws_root = Path(".")

    last = 0
    for match in _MD_IMG_RE.finditer(text):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "data:", "//", "#")):
            continue
        resolved = (base_dir / target).resolve()
        if not resolved.exists():
            continue
        pre = text[last : match.start()].strip()
        if pre:
            index += 1
            blocks.append(Block(kind="text", page=1, index=index, text=pre))
        try:
            rel = resolved.relative_to(ws_root).as_posix()
        except ValueError:
            rel = target
        index += 1
        ext = resolved.suffix.lower() or ".png"
        blocks.append(
            Block(
                kind="image",
                page=1,
                index=index,
                source_path=rel,
                mime_type=IMAGE_MIME_BY_EXT.get(ext, "image/png"),
            )
        )
        last = match.end()
    tail = text[last:].strip()
    if tail:
        index += 1
        blocks.append(Block(kind="text", page=1, index=index, text=tail))
    return blocks


# ---------------------------------------------------------------------------
# 新增格式解析器：统一「页 → list[Block]」的纯函数
#
# 每个解析器把字节直接转成块列表，不持有原生句柄：这些格式都是一次性全文解析，
# 没有 PDF 那样的惰性分页需求。返回值是「按页的块列表」，无页概念格式恒 1 页。
# 任何单块失败都降级为 unsupported 块，不让整份文档不可读。
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DocHandle:
    """静态格式的统一句柄：打开即解析完的「页 → 块」。"""

    pages: list[list[Block]]


def _text_blocks(texts: Sequence[str], page: int = 1) -> list[Block]:
    """把多段纯文本收敛为带序号的 text 块，丢弃空段。"""
    out: list[Block] = []
    for text in texts:
        body = _normalize(text).strip()
        if body:
            out.append(Block(kind="text", page=page, index=len(out) + 1, text=body))
    return out


def _decode_text(raw: bytes) -> str:
    """文本解码：优先 UTF-8（含 BOM），失败时 GB18030 兜底（中文文档常见）。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _table_block(page: int, index: int, rows: Sequence[Sequence[str]]) -> Block:
    return Block(
        kind="table",
        page=page,
        index=index,
        rows=tuple(tuple(str(cell).strip() for cell in row) for row in rows),
    )


def _parse_rtf(raw: bytes, **_unused: object) -> list[list[Block]]:
    from striprtf.striprtf import rtf_to_text

    # RTF 本身是 7-bit ASCII 转义，非 ASCII 由 \\uN 承载；latin-1 解码不破坏控制字。
    text = rtf_to_text(raw.decode("latin-1", errors="replace"))
    return [_text_blocks(text.split("\n\n"))]


def _parse_odt(raw: bytes, **_unused: object) -> list[list[Block]]:
    from odf import teletype
    from odf.opendocument import load

    doc = load(io.BytesIO(raw))
    blocks: list[Block] = []
    _walk_odf_text(doc.text, blocks, page=1, extract=teletype.extractText)
    return [blocks]


def _walk_odf_text(
    node: object,
    blocks: list[Block],
    *,
    page: int,
    extract: Callable[[object], str],
) -> None:
    """按文档顺序遍历 ODF 正文树，产出文本/表格块。

    只递归块级容器（文档体、列表、列表项），不进入表格内部——表格整体转成
    table 块，单元格文字不重复计入正文。仅看直接子节点会丢掉列表与表格。
    """
    from odf.table import TableCell, TableRow

    for child in getattr(node, "childNodes", ()):
        tag = getattr(child, "qname", (None, ""))[1]
        if tag == "table":
            rows = [
                [extract(cell) for cell in row.getElementsByType(TableCell)]
                for row in child.getElementsByType(TableRow)
            ]
            rows = [r for r in rows if any(cell.strip() for cell in r)]
            if rows:
                blocks.append(_table_block(page, len(blocks) + 1, rows))
        elif tag == "h":
            body = extract(child).strip()
            if body:
                level = int(child.getAttribute("outlinelevel") or 1)
                blocks.append(
                    Block(
                        kind="text",
                        page=page,
                        index=len(blocks) + 1,
                        text=f"{'#' * level} {body}",
                    )
                )
        elif tag == "p":
            body = extract(child).strip()
            if body:
                blocks.append(Block(kind="text", page=page, index=len(blocks) + 1, text=body))
        elif tag in ("list", "list-item"):
            _walk_odf_text(child, blocks, page=page, extract=extract)


def _parse_ods(raw: bytes, **_unused: object) -> list[list[Block]]:
    from odf import teletype
    from odf.opendocument import load
    from odf.table import Table, TableCell, TableRow

    doc = load(io.BytesIO(raw))
    blocks: list[Block] = []
    for sheet in doc.spreadsheet.getElementsByType(Table):
        name = sheet.getAttribute("name") or ""
        rows: list[list[str]] = []
        for row in sheet.getElementsByType(TableRow):
            cells = [teletype.extractText(c) for c in row.getElementsByType(TableCell)]
            if any(cell.strip() for cell in cells):
                rows.append(cells)
        if not rows:
            continue
        if name:
            blocks.append(Block(kind="text", page=1, index=len(blocks) + 1, text=f"# {name}"))
        blocks.append(_table_block(1, len(blocks) + 1, rows))
    return [blocks]


def _parse_odp(raw: bytes, **_unused: object) -> list[list[Block]]:
    from odf import teletype
    from odf.draw import Page
    from odf.opendocument import load
    from odf.text import P

    doc = load(io.BytesIO(raw))
    pages: list[list[Block]] = []
    for page_no, page in enumerate(doc.presentation.getElementsByType(Page), start=1):
        texts = [teletype.extractText(p) for p in page.getElementsByType(P)]
        pages.append(_text_blocks(texts, page=page_no))
    return pages or [[]]


def _parse_epub(raw: bytes, **_unused: object) -> list[list[Block]]:
    from bs4 import BeautifulSoup
    from ebooklib import ITEM_DOCUMENT, epub

    book = epub.read_epub(io.BytesIO(raw))
    pages: list[list[Block]] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        # nav/ncx 是目录页，不是正文：算作正文页会让读者读到满屏章节名。
        if item.get_name().split("/")[-1] in ("nav.xhtml", "toc.xhtml", "toc.ncx"):
            continue
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        if text.strip():
            pages.append(_text_blocks(text.split("\n")))
    return pages or [[]]


def _parse_html(raw: bytes, **_unused: object) -> list[list[Block]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_decode_text(raw), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return [_text_blocks(text.split("\n"))]


def _parse_mhtml(raw: bytes, **_unused: object) -> list[list[Block]]:
    """MHTML：解析 multipart，取 HTML 部件的正文（图片等资源不足以重建版面，忽略）。"""
    import email
    from email import policy

    message = email.message_from_bytes(raw, policy=policy.default)
    parts: list[str] = []
    for part in message.walk():
        content_type = part.get_content_type()
        if content_type not in ("text/html", "text/plain"):
            continue
        try:
            body = part.get_content()
        except Exception:  # noqa: BLE001 - 编码异常的部件跳过，不影响其它部件
            continue
        if isinstance(body, bytes):
            body = _decode_text(body)
        if content_type == "text/html":
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(body, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            body = soup.get_text("\n", strip=True)
        parts.extend(body.split("\n"))
    return [_text_blocks(parts)]


def _parse_csv(raw: bytes, **_unused: object) -> list[list[Block]]:
    import csv

    text = _decode_text(raw)
    # 分隔符按内容探测：TSV 用制表符，其余按逗号。
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return [[]]
    return [[_table_block(1, 1, rows)]]


def _parse_xls(raw: bytes, **_unused: object) -> list[list[Block]]:
    import xlrd

    book = xlrd.open_workbook(file_contents=raw)
    blocks: list[Block] = []
    for sheet in book.sheets():
        rows: list[list[str]] = []
        for r in range(sheet.nrows):
            cells = [str(v) for v in sheet.row_values(r)]
            if any(cell.strip() for cell in cells):
                rows.append(cells)
        if not rows:
            continue
        blocks.append(Block(kind="text", page=1, index=len(blocks) + 1, text=f"# {sheet.name}"))
        blocks.append(_table_block(1, len(blocks) + 1, rows))
    return [blocks]


_OFD_NS = {"ofd": "http://www.ofdspec.org/2016"}


def _ofd_member_path(base: Path, value: str) -> str:
    """把 OFD 里的引用解析成 zip 成员名。

    以 ``/`` 开头的是包内绝对路径（去前导斜杠即成员名）；否则相对 DocRoot
    所在目录。反斜杠一律按路径分隔符处理，与 zip 的 ``/`` 成员名对齐。
    """
    raw = value.strip().replace("\\", "/")
    if not raw:
        return ""
    if raw.startswith("/"):
        return raw.lstrip("/")
    return (base / raw).as_posix()


def _parse_ofd(
    raw: bytes,
    *,
    workspace_path: Path | None,
    source_rel_path: str,
) -> list[list[Block]]:
    """OFD：zip 包，``OFD.xml`` 的 DocRoot 指向文档描述，页在其中的 ``Pages``。

    结构按 GB/T 33190：``OFD.xml`` → ``DocBody/DocRoot``（如 ``Doc_0/Document.xml``）
    → 该文件里的 ``Pages/Page@BaseLoc`` 给出每页 ``Content.xml``，图片经
    ``Resources/MultiMedia@ID`` → ``MediaFile`` 映射到包内资源。路径均相对 DocRoot
    所在目录解析。

    页面图片落盘并产出 image 块，交由 read 的图片分支识别（含扫描件 OCR）；
    无 TextCode 且无图片的页返回 unsupported 块，由上层提示走 vision。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())
            doc_roots = [
                _ofd_member_path(Path("."), node.text or "")
                for node in ET.fromstring(zf.read("OFD.xml")).findall(".//ofd:DocRoot", _OFD_NS)
            ]
            doc_roots = [loc for loc in doc_roots if loc in names]
            if not doc_roots:
                raise ValueError("OFD 缺少文档描述文件（DocRoot 未指向包内文件）")

            pages: list[list[Block]] = []
            for doc_root in doc_roots:
                base = Path(doc_root).parent
                document = ET.fromstring(zf.read(doc_root))
                # ResourceID → 包内路径。取第一个 MediaFile：同一 MultiMedia 的多媒体
                # 附件（如多页扫描）不参与版面。
                media: dict[str, str] = {}
                for item in document.findall(".//ofd:MultiMedia", _OFD_NS):
                    res_id = item.get("ID")
                    files = [
                        _ofd_member_path(base, node.text or "")
                        for node in item.findall("ofd:MediaFile", _OFD_NS)
                    ]
                    if res_id and files and files[0]:
                        media[res_id] = files[0]

                for page in document.findall(".//ofd:Pages/ofd:Page", _OFD_NS):
                    page_no = len(pages) + 1
                    content_path = _ofd_member_path(base, page.get("BaseLoc") or "")
                    if content_path not in names:
                        pages.append(
                            [
                                Block(
                                    kind="unsupported",
                                    page=page_no,
                                    index=1,
                                    text="该页内容缺失（OFD 包内无对应 Content.xml）",
                                )
                            ]
                        )
                        continue
                    pages.append(
                        _ofd_page_blocks(
                            zf,
                            content_path,
                            page_no,
                            media,
                            workspace_path=workspace_path,
                            source_rel_path=source_rel_path,
                        )
                    )
            return pages or [[]]
    except zipfile.BadZipFile as exc:
        raise ValueError("OFD 文件损坏（不是有效的 zip 包）") from exc
    except ET.ParseError as exc:
        raise ValueError(f"OFD 结构非法：{exc}") from exc


def _ofd_page_blocks(
    zf: zipfile.ZipFile,
    content_path: str,
    page_no: int,
    media: dict[str, str],
    *,
    workspace_path: Path | None,
    source_rel_path: str,
) -> list[Block]:
    """解析一页 Content.xml：TextCode 转文本块，ImageObject 转图片块。"""
    content = ET.fromstring(zf.read(content_path))
    blocks: list[Block] = []
    for code in content.findall(".//ofd:TextCode", _OFD_NS):
        body = (code.text or "").strip()
        if body:
            blocks.append(
                Block(kind="text", page=page_no, index=len(blocks) + 1, text=_normalize(body))
            )
    image_counter = 0
    for obj in content.findall(".//ofd:ImageObject", _OFD_NS):
        resource = media.get(obj.get("ResourceID") or "")
        if resource is None:
            continue
        try:
            data = zf.read(resource)
        except KeyError:
            continue
        image_counter += 1
        source = None
        if workspace_path is not None:
            source = _save_image(
                workspace_path,
                source_rel_path,
                page_no * 1000 + image_counter,
                Path(resource).suffix.lower() or ".png",
                data,
            )
        blocks.append(
            Block(
                kind="image",
                page=page_no,
                index=len(blocks) + 1,
                source_path=source,
                mime_type=IMAGE_MIME_BY_EXT.get(Path(resource).suffix.lower(), "image/png"),
            )
        )
    return blocks


_STATIC_PARSERS: dict[str, Any] = {
    "rtf": _parse_rtf,
    "odt": _parse_odt,
    "ods": _parse_ods,
    "odp": _parse_odp,
    "epub": _parse_epub,
    "ofd": _parse_ofd,
    "html": _parse_html,
    "mhtml": _parse_mhtml,
    "csv": _parse_csv,
    "xls": _parse_xls,
}


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
def content_hash8(raw: bytes) -> str:
    return hashlib.sha1(raw).hexdigest()[:8]


def _parser_version() -> str:
    try:
        return f"pypdfium2={_pkg_version('pypdfium2')}"
    except Exception:  # noqa: BLE001 - 元数据缺失时退化为未知版本
        return "pypdfium2=unknown"


def cache_dir(workspace_path: Path, raw: bytes, source_rel_path: str) -> Path:
    """单文档缓存目录：``<root>/.work/cache/doc/<hash16>/``。

    键取内容哈希，同一内容复制到不同路径共用缓存；会话内文件随会话回收。
    """
    key = hashlib.sha1(raw).hexdigest()[:16]
    root = _session_root_of(workspace_path, source_rel_path) or workspace_path
    return root / ".work" / "cache" / "doc" / key


def _meta_path(target: Path) -> Path:
    return target / "meta.json"


def ensure_cache(
    target: Path,
    *,
    fmt: str,
    pages: int,
) -> dict[str, Any]:
    """确保缓存目录与 meta 可用；格式/页数/解析器版本变化时整目录重建。"""
    expected = {
        "fmt": fmt,
        "pages": pages,
        "parser_version": _parser_version(),
        "schema_version": SCHEMA_VERSION,
    }
    meta_file = _meta_path(target)
    if meta_file.exists():
        current: dict[str, Any] | None
        try:
            loaded = json.loads(meta_file.read_text(encoding="utf-8"))
            current = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            current = None
        # 只比较身份字段：page_digests / page_modality / vision_attempted 是随
        # 使用增长的附属数据，不参与重建判定。字段齐全时不写盘——每次 read
        # 都重写会让并发读互相覆盖对方的累积数据；仅缺字段时补写一次。
        if current is not None and all(current.get(k) == v for k, v in expected.items()):
            missing = []
            if not isinstance(current.get("page_modality"), dict):
                current["page_modality"] = {}
                missing.append("page_modality")
            if not isinstance(current.get("vision_attempted"), list):
                current["vision_attempted"] = []
                missing.append("vision_attempted")
            if missing:
                with _CACHE_LOCK:
                    # 重读后只补缺失键，避免覆盖并发写入的累积数据
                    try:
                        latest = json.loads(meta_file.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        latest = dict(current)
                    for key in missing:
                        latest.setdefault(key, current[key])
                    _atomic_write(meta_file, json.dumps(latest, ensure_ascii=False).encode("utf-8"))
            return current
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        meta_file,
        json.dumps(
            {**expected, "page_digests": {}, "page_modality": {}, "vision_attempted": []}
        ).encode("utf-8"),
    )
    return expected


def read_meta(target: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(_meta_path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _page_file(target: Path, page: int, tier: str) -> Path:
    suffix = ".vision.json" if tier == "vision" else ".json"
    return target / f"p{page}{suffix}"


def load_page(target: Path, page: int, tier: str = "text") -> list[Block] | None:
    """读取已缓存的页。

    命中条件：可解析、页号与文件名一致、且与 meta 里登记的摘要一致。摘要在
    写入时计算并记入 meta；`bash` 与文件工具共用会话根，缓存目录对模型可写，
    摘要不一致即视为被篡改的缓存，按未命中处理并重新解析。
    """
    path = _page_file(target, page, tier)
    try:
        payload = path.read_bytes()
        data = json.loads(payload)
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None

    meta = read_meta(target) or {}
    digests = meta.get("page_digests") or {}
    expected = digests.get(path.name)
    if expected is not None and expected != _digest(payload):
        return None

    blocks = [_block_from_dict(item) for item in data]
    if any(block.page != page for block in blocks):
        return None
    return blocks


def save_page(target: Path, page: int, tier: str, blocks: list[Block]) -> None:
    payload = json.dumps([_block_to_dict(block) for block in blocks], ensure_ascii=False).encode(
        "utf-8"
    )
    name = _page_file(target, page, tier).name
    with _CACHE_LOCK:
        _atomic_write(target / name, payload)
        _register_digest(target, name, _digest(payload))


def _register_digest(target: Path, name: str, digest: str) -> None:
    """把页文件摘要登记进 meta，供读取侧校验完整性。"""
    meta_file = _meta_path(target)
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    digests = meta.setdefault("page_digests", {})
    digests[name] = digest
    _atomic_write(meta_file, json.dumps(meta, ensure_ascii=False).encode("utf-8"))


def record_page_modality(target: Path, page: int, modality: dict[str, Any]) -> None:
    """把某页的模态信息累积进 meta（已探测的页不重算、不覆盖）。"""
    with _CACHE_LOCK:
        meta_file = _meta_path(target)
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        table = meta.setdefault("page_modality", {})
        if str(page) in table:
            return
        table[str(page)] = modality
        _atomic_write(meta_file, json.dumps(meta, ensure_ascii=False).encode("utf-8"))


def detected_scanned_pages(target: Path) -> list[int]:
    """``page_modality`` 中已判定为扫描页的页号（升序）。"""
    meta = read_meta(target) or {}
    table = meta.get("page_modality") or {}
    pages: list[int] = []
    for key, value in table.items():
        try:
            page = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and value.get("scanned"):
            pages.append(page)
    return sorted(pages)


def record_vision_attempt(target: Path, page: int) -> None:
    """记录「该页已尝试 OCR」。附属数据，不参与缓存重建判定。"""
    with _CACHE_LOCK:
        meta_file = _meta_path(target)
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        attempted = meta.setdefault("vision_attempted", [])
        if page in attempted:
            return
        attempted.append(page)
        attempted.sort()
        _atomic_write(meta_file, json.dumps(meta, ensure_ascii=False).encode("utf-8"))


def vision_attempted(target: Path) -> set[int]:
    meta = read_meta(target) or {}
    attempted = meta.get("vision_attempted") or []
    return {int(p) for p in attempted if isinstance(p, int) or str(p).isdigit()}


def _digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


def _atomic_write(path: Path, data: bytes) -> None:
    # 线程/进程并发写同一页时，固定 tmp 名会互删导致 os.replace 报 ENOENT。
    # 附加线程与 pid 让各写入者的临时文件互不干扰。
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def cached_pages(target: Path) -> list[int]:
    """已解析的页号（text 档或 vision/OCR 档任一存在即可，升序去重）。"""
    pages: set[int] = set()
    for path in target.glob("p*.json"):
        stem = path.name[len("p") :]
        if stem.endswith(".vision.json"):
            stem = stem[: -len(".vision.json")]
        else:
            stem = stem[: -len(".json")]
        if stem.isdigit():
            pages.add(int(stem))
    return sorted(pages)


# ---------------------------------------------------------------------------
# 定位符
# ---------------------------------------------------------------------------
_LOCATOR_RE = re.compile(
    r"^doc:(?P<hash>[0-9a-f]{8})/p(?P<page>\d+)/b(?P<index>\d+)@(?P<tier>text|vision)(?:/c(?P<offset>\d+))?$"
)


@dataclass(frozen=True, slots=True)
class Cursor:
    hash8: str
    page: int
    index: int
    tier: str
    char_offset: int = 0

    def __str__(self) -> str:
        base = f"doc:{self.hash8}/p{self.page}/b{self.index}@{self.tier}"
        return f"{base}/c{self.char_offset}" if self.char_offset else base


def parse_locator(locator: str) -> Cursor:
    match = _LOCATOR_RE.match(locator.strip())
    if match is None:
        raise ValueError(
            f"定位符格式错误：{locator!r}；期望 doc:<hash8>/p<页码>/b<块号>@<text|vision>[/c<偏移>]"
        )
    page = int(match.group("page"))
    index = int(match.group("index"))
    if page < 1 or index < 1:
        raise ValueError("页码与块号从 1 开始")
    return Cursor(
        hash8=match.group("hash"),
        page=page,
        index=index,
        tier=match.group("tier"),
        char_offset=int(match.group("offset") or 0),
    )


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _soft_cut(text: str, budget: int) -> int:
    """在自然边界切分，返回切点；找不到合适边界才硬切。

    切点须超过预算一半，避免切出过短的块。
    """
    if len(text) <= budget:
        return len(text)
    floor = budget // 2
    for marker in ("\n\n", "\n", "。", "；", ". "):
        pos = text.rfind(marker, floor, budget)
        if pos > 0:
            return pos + len(marker)
    return budget


def render_block(block: Block, char_offset: int = 0) -> str:
    if block.kind == "table":
        body = "\n".join("    | " + " | ".join(row) + " |" for row in block.rows)
        return f"b{block.index} [table]\n{body}"
    if block.kind == "image":
        path = to_model_path(block.source_path) if block.source_path else "(未落盘)"
        return f"b{block.index} [image: {path} — 使用 read 识别图片内容]"
    if block.kind == "unsupported":
        return f"b{block.index} [unsupported] {block.text}"
    text = block.text[char_offset:]
    return _text_label(block, char_offset) + text


def _text_label(block: Block, char_offset: int = 0) -> str:
    """文本块的行首标签：块号 + 续读偏移 + OCR 来源标记。"""
    prefix = f"b{block.index} " if not char_offset else f"b{block.index} (续 {char_offset}+) "
    if block.is_ocr:
        prefix += "[OCR] "
    return prefix


def render_blocks(
    blocks: list[Block],
    *,
    hash8: str,
    tier: str,
    start: Cursor | None = None,
    char_budget: int = _WINDOW_CHAR_BUDGET,
) -> tuple[str, Cursor | None]:
    """渲染块列表为模型可读文本，超出预算时返回续读游标。

    ``start`` 指定从某块（及其字符偏移）开始渲染，用于续读。预算按字符计；
    超长单块在自然边界切分，游标记录字符偏移。返回的游标指向下一条未渲染的
    内容（同块后半段或下一块）。
    """
    start_at = 0
    start_offset = 0
    if start is not None:
        for pos, block in enumerate(blocks):
            if block.index == start.index and block.page == start.page:
                start_at = pos
                start_offset = start.char_offset
                break

    parts: list[str] = []
    used = 0
    last_page: int | None = None
    for pos in range(start_at, len(blocks)):
        block = blocks[pos]
        offset = start_offset if pos == start_at else 0
        rendered = render_block(block, char_offset=offset)
        # 批量读取跨页时插入页边界：块号只在页内唯一，不分页模型无法区分。
        if last_page is not None and block.page != last_page:
            rendered = f"--- p{block.page} ---\n" + rendered
        last_page = block.page
        if used + len(rendered) <= char_budget:
            parts.append(rendered)
            used += len(rendered) + 1
            continue
        remaining = char_budget - used
        if block.kind == "text" and remaining > 200:
            tail = block.text[offset:]
            cut = _soft_cut(tail, remaining)
            if cut > 0:
                parts.append(_partial_text(block, offset, cut))
                return "\n\n".join(parts), Cursor(
                    hash8=hash8,
                    page=block.page,
                    index=block.index,
                    tier=tier,
                    char_offset=offset + cut,
                )
        return "\n\n".join(parts), Cursor(
            hash8=hash8, page=block.page, index=block.index, tier=tier
        )
    return "\n\n".join(parts), None


def _partial_text(block: Block, offset: int, cut: int) -> str:
    return _text_label(block, offset) + block.text[offset : offset + cut]


def render_blocks_by_page(
    blocks: list[Block],
    *,
    hash8: str,
    tier: str,
    char_budget: int = _WINDOW_CHAR_BUDGET,
) -> tuple[str, int | None, Cursor | None]:
    """按页装填字符预算，返回 ``(text, 最后完整渲染的页, 单页溢出游标)``。

    批量读取以页为窗口边界：一页放不下整个预算时**不**切页，而是停在上一页，
    由调用方给「下一页」指令（方案 §4.10）。只有首屏连一页都放不下时，才退回
    块级切分并返回 ``after`` 字符游标——两种续读指令互斥。
    """
    pages: list[int] = []
    grouped: dict[int, list[Block]] = {}
    for block in blocks:
        if block.page not in grouped:
            grouped[block.page] = []
            pages.append(block.page)
        grouped[block.page].append(block)

    parts: list[str] = []
    used = 0
    last_page: int | None = None
    for page in pages:
        page_text, _ = render_blocks(grouped[page], hash8=hash8, tier=tier, char_budget=char_budget)
        chunk = f"--- p{page} ---\n{page_text}" if last_page is not None else page_text
        if used + len(chunk) <= char_budget:
            parts.append(chunk)
            used += len(chunk) + 2
            last_page = page
            continue
        if last_page is None:
            # 单页就超出预算：退回块级切分，给字符游标
            text, cursor = render_blocks(
                grouped[page], hash8=hash8, tier=tier, char_budget=char_budget
            )
            return text, None, cursor
        break
    return "\n\n".join(parts), last_page, None


def render_page_header(
    path: str,
    page: int,
    total_pages: int,
    tier: str,
    *,
    page_end: int | None = None,
    ocr_pages: Sequence[int] = (),
    scanned_pages: Sequence[int] = (),
) -> str:
    if ocr_pages:
        listed = "、".join(f"p{p}" for p in ocr_pages[:10])
        source = f"，来源 OCR：{listed}（可能有识别错误）"
    else:
        source = ""
    span = f"当前 p{page}" if page_end in (None, page) else f"本次 p{page}–p{page_end}"
    header = f"[read: {path} — 共 {total_pages} 页，{span}（tier={tier}{source}）]"
    # 只列已探测到的扫描页：不假装知道全貌（惰性探测）。
    if tier == "text" and scanned_pages:
        listed = "、".join(f"p{p}" for p in scanned_pages[:10])
        more = " 等" if len(scanned_pages) > 10 else ""
        header += f"\n[已发现的扫描页：{listed}{more}（其余页未探测）]"
    return header


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------
def search_blocks(
    target: Path,
    pattern: str,
    *,
    case_insensitive: bool,
    max_matches: int,
    hash8: str,
    raw: bytes | None = None,
    fmt: str | None = None,
) -> tuple[list[dict[str, str]], list[int], int]:
    """在已解析页上按块检索，返回 (matches, 已解析页, 总页数)。

    覆盖 text 档与 vision 档（OCR）页：OCR 结果同样是可检索正文，跳过它会让
    「OCR 之后 grep 仍报未覆盖」成为死循环。命中 OCR 页时定位符标 ``@vision``，
    ``line`` 前缀 ``[OCR]``。

    只扫已解析页：未解析页不计入，由调用方向模型声明覆盖范围。给出 ``raw`` 与
    ``fmt`` 时，若缓存尚未建立 meta，则在此补建——否则未解析文档会报出
    ``total_pages=0``，让模型误以为文档为空。
    """
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"正则无效：{exc}") from exc

    meta = read_meta(target)
    if meta is None and raw is not None and fmt is not None:
        # 只为建 meta 取页数，不解析块：故意不传 workspace，避免无谓落盘内嵌图。
        opened = open_doc(raw, fmt)
        try:
            meta = ensure_cache(target, fmt=fmt, pages=page_count(opened, fmt))
        finally:
            close_doc(opened, fmt)

    total_pages = int((meta or {}).get("pages") or 0)
    pages = cached_pages(target)
    matches: list[dict[str, str]] = []
    for page in pages:
        # 每页只取一个权威来源：text 有可读内容时以 text 为准，否则用 OCR 结果。
        # 两档都搜会让同一页重复命中（vision 是对整页的覆盖，不是补充）。
        text_blocks = load_page(target, page, "text")
        if text_blocks is not None and any(
            block.kind in ("text", "table") for block in text_blocks
        ):
            sources = [("text", text_blocks)]
        else:
            ocr = load_page(target, page, "vision")
            sources = [("vision", ocr)] if ocr is not None else []
        for tier, blocks in sources:
            for block in blocks:
                if block.kind not in ("text", "table"):
                    continue
                body = (
                    block.text
                    if block.kind == "text"
                    else "\n".join(" | ".join(row) for row in block.rows)
                )
                for line in body.split("\n"):
                    if regex.search(line) and len(matches) < max_matches:
                        snippet = line.strip()[:500]
                        if block.is_ocr:
                            snippet = "[OCR] " + snippet
                        matches.append(
                            {
                                "path": "",
                                "line_number": str(
                                    Cursor(
                                        hash8=hash8,
                                        page=page,
                                        index=block.index,
                                        tier=tier,
                                    )
                                ),
                                "line": snippet,
                            }
                        )
    return matches, pages, total_pages
