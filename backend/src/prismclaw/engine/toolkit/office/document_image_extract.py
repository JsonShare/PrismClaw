"""Document reader that extracts text and images in document order.

When an agent reads a document (docx / pdf / markdown), this module extracts
both text and any inline images. Images are persisted to the workspace system
area ``.work/cache/extracted/<source-key>/`` (never beside the user's source
file) and referenced by ``source_path`` only — base64 is no longer extracted
(images go through the read_image tool, never the main context).

The single public entry point is :func:`extract_document_blocks`.
Returns list[TextBlock | ImageBlock] — no intermediate type, no path markers.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path
from typing import Any

from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.agent.data_types import ImageBlock

# ---- OOXML namespaces (XML namespace identifiers, NOT network URLs) ----------
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_IMAGE_EXT_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    """Return the local name of a (possibly namespaced) ElementTree tag."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _attr_local(elem: Any, local_name: str) -> str | None:
    """Fetch an attribute by local name regardless of its namespace prefix."""
    for key, value in elem.attrib.items():
        if _local(key) == local_name:
            return value
    return None


def _guess_mime_type(ext: str) -> str:
    return _IMAGE_EXT_MIME.get(ext.lower(), "image/png")


def _save_image_block(
    workspace_path: Path,
    source_rel_path: str,
    counter: int,
    ext: str,
    data: bytes,
) -> str:
    """Persist image to the workspace cache and return its workspace-relative path.

    落点固定在 ``<ws>/.work/cache/extracted/<source-key>/``：图片是引擎副产物，
    不能散落在用户源文件旁边（D7）。``source-key`` 由源文件的绝对路径派生，
    同一源文件跨会话稳定命中同一目录，re-extract 时可按内容比对跳过重写。
    """
    src = Path(source_rel_path)
    abs_src = (workspace_path / src).resolve(strict=False)
    source_key = hashlib.sha1(str(abs_src).encode("utf-8")).hexdigest()[:16]
    out_dir = workspace_path / ".work" / "cache" / "extracted" / source_key
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{src.stem}_img_{counter:03d}{ext}"
    out_path = out_dir / fname
    # 内容未变时跳过重写：OCR 缓存以 (size, ctime_ns, ino) 为指纹，无条件重写
    # 会使每次 re-extract（上传解析 / read 的文档分支）都作废全部图片的 OCR 缓存，
    # 并导致 analysis.md 的 OCR 回填永远落空（回填查缓存时图片刚被重写）。
    try:
        if out_path.read_bytes() == data:
            return out_path.relative_to(workspace_path).as_posix()
    except OSError:
        pass  # 不存在或不可读 → 正常写入
    out_path.write_bytes(data)
    return out_path.relative_to(workspace_path).as_posix()


def _make_image_block(
    workspace_path: Path,
    source_rel_path: str,
    counter: int,
    ext: str,
    data: bytes,
) -> ImageBlock:
    """Create an ImageBlock from raw image bytes.

    Persists to disk and references by source_path only — data is empty
    (images go through read_image, not the main context).
    """
    ws_rel = _save_image_block(workspace_path, source_rel_path, counter, ext, data)
    return ImageBlock(
        type="image",
        data="",
        mime_type=_guess_mime_type(ext),
        source_path=ws_rel,
    )


def _make_text_block(text: str) -> TextBlock:
    return TextBlock(type="text", text=text)


# ---------------------------------------------------------------------------
# docx
# ---------------------------------------------------------------------------
def _extract_docx_blocks(
    raw: bytes,
    *,
    source_rel_path: str,
    workspace_path: Path,
) -> list[TextBlock | ImageBlock]:
    blocks: list[TextBlock | ImageBlock] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())

            # relationship id -> media target
            rel_map: dict[str, str] = {}
            rels_name = "word/_rels/document.xml.rels"
            if rels_name in names:
                import xml.etree.ElementTree as ET

                rel_root = ET.fromstring(zf.read(rels_name))
                for rel in rel_root:
                    if _local(rel.tag) != "Relationship":
                        continue
                    rid = rel.get("Id")
                    target = rel.get("Target")
                    mode = rel.get("TargetMode")
                    if rid and target and mode != "External":
                        rel_map[rid] = target

            if "word/document.xml" not in names:
                return blocks

            import xml.etree.ElementTree as ET

            doc_root = ET.fromstring(zf.read("word/document.xml"))
            body = None
            for el in doc_root.iter():
                if _local(el.tag) == "body":
                    body = el
                    break
            if body is None:
                return blocks

            img_counter = 0

            def emit_image(rid: str) -> None:
                nonlocal img_counter
                target = rel_map.get(rid)
                if not target:
                    return
                media_path = target if target.startswith("/") else "word/" + target.lstrip("./")
                if media_path not in names:
                    media_path = target.lstrip("/")
                    if media_path not in names:
                        return
                data = zf.read(media_path)
                ext = Path(media_path).suffix.lower() or ".png"
                img_counter += 1
                blocks.append(
                    _make_image_block(
                        workspace_path, source_rel_path, img_counter, ext, data
                    )
                )

            _walk_docx(body, emit_image, blocks)
    except zipfile.BadZipFile:
        return [_make_text_block("[不是有效的 .docx（zip）文件]")]
    return blocks


def _walk_docx(
    node: Any,
    emit_image,
    blocks: list[TextBlock | ImageBlock],
) -> None:
    """Recursively emit text runs and inline images in document order."""
    tag = _local(node.tag)

    if tag == "p":  # <w:p> paragraph: buffer text, flush around images
        para_buf: list[str] = []
        para_id = _attr_local(node, "paraId")
        if para_id:
            para_buf.append(f"[{para_id}] ")
        _walk_paragraph_content(node, emit_image, blocks, para_buf)
        paragraph_text = "".join(para_buf)
        if paragraph_text:
            blocks.append(_make_text_block(paragraph_text))
        blocks.append(_make_text_block("\n"))
        return

    if tag == "tr":  # table row
        for child in node:
            _walk_docx(child, emit_image, blocks)
        return

    if tag == "tc":  # table cell
        for child in node:
            _walk_docx(child, emit_image, blocks)
        blocks.append(_make_text_block(" | "))
        return

    # default: descend
    for child in node:
        _walk_docx(child, emit_image, blocks)


def _walk_paragraph_content(
    node: Any,
    emit_image,
    blocks: list[TextBlock | ImageBlock],
    para_buf: list[str],
) -> None:
    """Walk a paragraph's children, buffering text and emitting images inline."""
    for child in node:
        tag = _local(child.tag)
        if tag == "t":  # <w:t> text run
            if child.text:
                para_buf.append(child.text)
        elif tag == "drawing":  # <w:drawing> -> embedded image
            if para_buf:
                blocks.append(_make_text_block("".join(para_buf)))
                para_buf.clear()
            blip = None
            for d in child.iter():
                if _local(d.tag) == "blip":
                    blip = d
                    break
            if blip is not None:
                rid = _attr_local(blip, "embed")
                if rid:
                    emit_image(rid)
        else:
            _walk_paragraph_content(child, emit_image, blocks, para_buf)


# ---------------------------------------------------------------------------
# pdf
# ---------------------------------------------------------------------------
def _extract_pdf_blocks(
    raw: bytes,
    *,
    source_rel_path: str,
    workspace_path: Path,
) -> list[TextBlock | ImageBlock]:
    try:
        import pypdf  # type: ignore

        PdfReader = pypdf.PdfReader
    except ImportError:
        try:
            import PyPDF2  # type: ignore

            PdfReader = PyPDF2.PdfReader
        except ImportError:
            return [
                _make_text_block(
                    "[读取 PDF 需要 'pypdf' 或 'PyPDF2']"
                )
            ]

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        return [_make_text_block(f"[解析 PDF 失败：{exc}]")]

    num_pages = len(reader.pages)
    blocks: list[TextBlock | ImageBlock] = [
        _make_text_block(
            f"[PDF 文档：{Path(source_rel_path).name} — 共 {num_pages} 页]\n"
        )
    ]
    img_counter = 0
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            blocks.append(
                _make_text_block(f"--- 第 {i + 1} 页 ---\n{text}\n")
            )
        try:
            imgs = list(getattr(page, "images", []) or [])
        except Exception:  # noqa: BLE001
            imgs = []
        for img in imgs:
            try:
                data = img.data
                ext = Path(getattr(img, "name", "image.png")).suffix.lower() or ".png"
            except Exception:  # noqa: BLE001
                continue
            if not data:
                continue
            img_counter += 1
            blocks.append(
                _make_image_block(
                    workspace_path, source_rel_path, img_counter, ext, data
                )
            )
    return blocks


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def _extract_markdown_blocks(
    text: str,
    *,
    source_rel_path: str,
    workspace_path: Path,
) -> list[TextBlock | ImageBlock]:
    src = Path(source_rel_path)
    base_dir = workspace_path / src.parent if src.parent != Path(".") else workspace_path
    base_dir = base_dir.resolve()
    ws_root = workspace_path.resolve()

    blocks: list[TextBlock | ImageBlock] = []
    last = 0
    for m in _MD_IMG_RE.finditer(text):
        target = m.group(1).strip()
        if target.startswith(("http://", "https://", "data:", "//", "#")):
            continue
        rel = target[2:] if target.startswith("./") else target
        resolved = (base_dir / rel).resolve()
        if not resolved.exists():
            continue
        pre = text[last : m.start()]
        if pre:
            blocks.append(_make_text_block(pre))
        try:
            rel_ws = resolved.relative_to(ws_root).as_posix()
        except ValueError:
            rel_ws = target
        # Markdown 图片已在磁盘上，仅记录 source_path（read_image 读取，不进主上下文）。
        # 不再 read_bytes/提取 base64：图片统一走工具，提取是无效逻辑。
        ext = resolved.suffix.lower() or ".png"
        blocks.append(
            ImageBlock(
                type="image",
                data="",
                mime_type=_guess_mime_type(ext),
                source_path=rel_ws,
            )
        )
        last = m.end()
    tail = text[last:]
    if tail:
        blocks.append(_make_text_block(tail))
    return blocks


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
def extract_document_blocks(
    raw: bytes,
    fmt: str,
    *,
    source_rel_path: str,
    workspace_path: Path,
) -> list[TextBlock | ImageBlock]:
    """Extract a document into ordered blocks (text + image).

    ``fmt`` is one of ``"docx"`` / ``"pdf"`` / ``"markdown"`` / ``"md"``.
    Image blocks carry an empty ``data`` and a workspace-relative
    ``source_path`` (images go through read_image, not the main context).
    """
    if fmt == "docx":
        return _extract_docx_blocks(
            raw, source_rel_path=source_rel_path, workspace_path=workspace_path
        )
    if fmt == "pdf":
        return _extract_pdf_blocks(
            raw, source_rel_path=source_rel_path, workspace_path=workspace_path
        )
    if fmt in ("markdown", "md"):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        return _extract_markdown_blocks(
            text, source_rel_path=source_rel_path, workspace_path=workspace_path
        )
    # fallback: treat as utf-8 text
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return [_make_text_block(text)]
