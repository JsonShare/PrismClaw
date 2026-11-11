"""read 工具文档分支（read_office.py）图文解析回归测试。

背景：commit a17c6b5 重构时 read_office.py 漏 import TextBlock，导致
`_blocks_to_text` / `_write_analysis_md` 对所有 docx/pdf/md 必抛
NameError（被宽 except 吞成 "failed to parse"）。该模块此前
零测试覆盖——本文件就是那道缺失的防线。

M1 合并后：独立工具 OfficeReadTool 已删除，read_office.py 成为 read
工具的文档分支（read_document_branch）。本文件覆盖两层：
1. 模块级解析函数（_blocks_to_text / _write_analysis_md / OCR 缓存保持）；
2. ReadTool 的分派行为（文档分派 / image 指路 / legacy office 拦截 /
   纯文本路径不受影响）。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prismclaw.schema.agent.data_types import ImageBlock, TextBlock  # noqa: E402
from prismclaw.schema.content.content_types import ToolResult  # noqa: E402
from prismclaw.engine.toolkit.builtin.file_tools import ReadTool  # noqa: E402
from prismclaw.engine.toolkit.file.read_office import (  # noqa: E402
    _blocks_to_text,
    _fmt_for_path,
    _write_analysis_md,
)
from prismclaw.engine.toolkit.office.document_image_extract import (  # noqa: E402
    extract_document_blocks,
)


def _make_blocks() -> list:
    """模拟 extract_document_blocks 的真实输出：文本-图片-文本交错。"""
    return [
        TextBlock(type="text", text="段落一"),
        ImageBlock(
            type="image", data="", mime_type="image/jpeg",
            source_path="uploads/.extracted/doc_img_001.jpeg",
        ),
        TextBlock(type="text", text="段落二"),
        ImageBlock(
            type="image", data="", mime_type="image/jpeg",
            source_path="uploads/.extracted/doc_img_002.jpeg",
        ),
    ]


def test_blocks_to_text_preserves_order_and_image_refs():
    """文本直拼 + 图片引用占位，顺序不乱、引用带 source_path。"""
    text = _blocks_to_text(_make_blocks())
    assert "段落一" in text and "段落二" in text
    assert "uploads/.extracted/doc_img_001.jpeg" in text
    assert "uploads/.extracted/doc_img_002.jpeg" in text
    assert text.index("段落一") < text.index("doc_img_001.jpeg") < text.index("段落二")


def test_blocks_to_text_real_docx(tmp_path):
    """真实 docx（内嵌图片）走完整 extract → to_text 链路不抛 NameError。

    用 zipfile 在测试内构造最小 docx，不依赖外部文件。
    """
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<w:body><w:p><w:r><w:t>标题文本</w:t></w:r></w:p></w:body></w:document>",
        )
        zf.writestr("word/_rels/document.xml.rels", '<?xml version="1.0"?><Relationships/>')
        # 一张 1x1 PNG
        import base64
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
            "YGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
        )
        zf.writestr("word/media/image1.png", png)

    blocks = extract_document_blocks(
        buf.getvalue(), "docx",
        source_rel_path="uploads/test.docx", workspace_path=tmp_path,
    )
    text = _blocks_to_text(blocks)  # 修复前此处抛 NameError
    assert "标题文本" in text


def test_write_analysis_md_persists(tmp_path):
    """analysis.md 必须真实落盘（修复前此函数抛 NameError，文件从未写出）。"""
    out = tmp_path / "out.analysis.md"
    _write_analysis_md(_make_blocks(), out, workspace_path=str(tmp_path))
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "段落一" in content and "段落二" in content
    assert "[图片: doc_img_001.jpeg]" in content


def test_fmt_for_path_covers_supported_suffixes():
    assert _fmt_for_path("a.docx") == "docx"
    assert _fmt_for_path("a.PDF") == "pdf"
    assert _fmt_for_path("a.md") == "markdown"
    assert _fmt_for_path("a.xlsx") == "xlsx"
    assert _fmt_for_path("a.pptx") == "pptx"
    assert _fmt_for_path("a.txt") is None


# ---------------------------------------------------------------------------
# OCR 缓存保持（修 _save_image_block 无条件重写导致缓存全量失效的缺陷）
# ---------------------------------------------------------------------------

_DOC_XML = (
    '<?xml version="1.0"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:drawing><blip embed=\"rId1\"/></w:drawing></w:r></w:p></w:body>"
    "</w:document>"
)
_RELS_XML = (
    '<?xml version="1.0"?>'
    '<Relationships><Relationship Id="rId1" Target="media/image1.png"/></Relationships>'
)


def _make_png(rgb: bytes) -> bytes:
    """构造 1x1 最小 PNG（颜色由 rgb 决定，用于区分内容差异）。"""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + rgb)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _build_docx_with_image(image_bytes: bytes) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", _DOC_XML)
        zf.writestr("word/_rels/document.xml.rels", _RELS_XML)
        zf.writestr("word/media/image1.png", image_bytes)
    return buf.getvalue()


def _extract_one_image(raw: bytes, ws) -> str:
    blocks = extract_document_blocks(
        raw, "docx", source_rel_path="uploads/t.docx", workspace_path=ws
    )
    return next(b.source_path for b in blocks if b.type == "image")


def test_reextract_preserves_ocr_cache(tmp_path):
    """内容未变时 re-extract 不得重写图片——ctime/ino 指纹保持，OCR 缓存命中。"""
    from prismclaw.engine.toolkit.file.ocr_cache import read_ocr_cache, write_ocr_cache

    raw = _build_docx_with_image(_make_png(b"\x00\x00\x00"))
    img_rel = _extract_one_image(raw, tmp_path)
    img_path = tmp_path / img_rel
    st1 = img_path.stat()

    write_ocr_cache(str(tmp_path), img_rel, "OCR_TEXT", "test-model")

    _extract_one_image(raw, tmp_path)  # 模拟 read / 再次上传的 re-extract
    st2 = img_path.stat()

    assert (st1.st_ctime_ns, st1.st_ino) == (st2.st_ctime_ns, st2.st_ino), (
        "内容未变却重写了图片 → OCR 缓存指纹失效"
    )
    assert read_ocr_cache(str(tmp_path), img_rel) == "OCR_TEXT"


def test_reextract_rewrites_when_image_content_changes(tmp_path):
    """图片内容变化时必须重写（缓存应失效、文件应为新内容）。"""
    raw_v1 = _build_docx_with_image(_make_png(b"\x00\x00\x00"))
    img_rel = _extract_one_image(raw_v1, tmp_path)
    assert (tmp_path / img_rel).read_bytes() == _make_png(b"\x00\x00\x00")

    raw_v2 = _build_docx_with_image(_make_png(b"\xff\x00\x00"))
    _extract_one_image(raw_v2, tmp_path)
    assert (tmp_path / img_rel).read_bytes() == _make_png(b"\xff\x00\x00")


# ---------------------------------------------------------------------------
# ReadTool 分派行为（M1 合并：read 吸收 read_office 后的路由）
# ---------------------------------------------------------------------------

_DOC_TEXT_XML = (
    '<?xml version="1.0"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>标题文本</w:t></w:r></w:p></w:body>"
    "</w:document>"
)


def _build_docx_with_text() -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", _DOC_TEXT_XML)
        zf.writestr("word/_rels/document.xml.rels", '<?xml version="1.0"?><Relationships/>')
    return buf.getvalue()


def _make_read_tool():
    from prismclaw.engine.toolkit.file.workspace import WorkspaceResolver
    from prismclaw.schema.config.config import WorkspaceConfig

    return ReadTool(WorkspaceResolver(WorkspaceConfig()))


def _make_read_ctx(tmp_path: Path):
    from prismclaw.engine.runtime.in_process_client import InProcessRuntimeClient
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext

    return ToolContext(
        workspace_id="ws-test",
        workspace_path=tmp_path,
        runtime=InProcessRuntimeClient(),
    )


def _run_read(tool: ReadTool, args: dict, ctx) -> ToolResult:
    import asyncio

    return asyncio.run(tool.execute({**args, "_call_id": "c1"}, ctx))


def _text_of(result: ToolResult) -> str:
    return "".join(b.text for b in result.content if isinstance(b, TextBlock))


def test_read_dispatches_docx_to_document_branch(tmp_path):
    """read(.docx) 走文档分支：header 标记 document，正文含提取文本。"""
    (tmp_path / "doc.docx").write_bytes(_build_docx_with_text())
    result = _run_read(
        _make_read_tool(), {"file_path": "doc.docx"}, _make_read_ctx(tmp_path)
    )
    assert not result.is_error
    text = _text_of(result)
    assert text.startswith("[read：doc.docx — 文档，第 1-")
    assert "标题文本" in text


def test_read_image_formats_dispatch_to_vision_branch(tmp_path, monkeypatch):
    """read(.png) 分派到视觉分支（原 read_image 工具已并入 read）。"""
    import prismclaw.engine.toolkit.file.read_image as read_image_mod
    from prismclaw.engine.toolkit.registry.tool_runtime import text_result

    called: dict = {}

    async def _fake_branch(*, call_id, raw_path, question, context, resolver):
        called["raw_path"] = raw_path
        called["question"] = question
        return text_result(call_id, "vision text")

    monkeypatch.setattr(read_image_mod, "read_image_branch", _fake_branch)

    result = _run_read(
        _make_read_tool(),
        {"file_path": "pic.png", "question": "图里有什么？"},
        _make_read_ctx(tmp_path),
    )
    assert not result.is_error
    assert called["raw_path"] == "pic.png"
    assert called["question"] == "图里有什么？"
    assert "vision text" in _text_of(result)


def test_read_legacy_office_formats_blocked(tmp_path):
    """read(.doc/.xls/.ppt) 给出可行动的转换提示，而非 UnicodeDecodeError。"""
    for ext in (".doc", ".xls", ".ppt"):
        result = _run_read(
            _make_read_tool(), {"file_path": f"old{ext}"}, _make_read_ctx(tmp_path)
        )
        assert result.is_error, ext
        assert "旧式二进制 Office 格式" in _text_of(result), ext


def test_read_text_path_unchanged_for_py(tmp_path):
    """合并后纯文本路径行为不变：正常读取 + 分页元数据。"""
    (tmp_path / "code.py").write_text("def main():\n    return 42\n", encoding="utf-8")
    result = _run_read(
        _make_read_tool(), {"file_path": "code.py"}, _make_read_ctx(tmp_path)
    )
    assert not result.is_error
    text = _text_of(result)
    assert "def main():" in text
    assert (result.metadata or {}).get("total_lines") == 2


def test_read_dispatches_md_to_document_branch(tmp_path):
    """read(.md) 走文档分支（M1 设计决策）：header 标记 document，正文含原文。

    注意与文本路径的观测差异（设计内）：.md 不进 read_file_state、
    无 read_epoch metadata、无单行截断——分支归属断言是关键防线。
    """
    (tmp_path / "notes.md").write_text(
        "# 标题\n\n正文段落，含 [图](img.png) 引用。\n", encoding="utf-8"
    )
    result = _run_read(
        _make_read_tool(), {"file_path": "notes.md"}, _make_read_ctx(tmp_path)
    )
    assert not result.is_error
    text = _text_of(result)
    assert text.startswith("[read：notes.md — 文档，第 1-")
    assert "正文段落" in text
