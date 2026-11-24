"""read 工具文档分支（read_office.py）行为测试。

覆盖 read 的分派与文档分支的对外契约：
1. ReadTool 的路由（文档 / 图片 / 旧式 Office / 纯文本）；
2. read_document_branch 的分页、续读游标、格式约束。

解析层的块级断言在 test_document_reader.py。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prismclaw.engine.toolkit.builtin.file_tools import ReadTool  # noqa: E402
from prismclaw.engine.toolkit.file.read_office import _fmt_for_path  # noqa: E402
from prismclaw.engine.toolkit.office.document_reader import DOC_FORMATS  # noqa: E402
from prismclaw.schema.agent.data_types import TextBlock  # noqa: E402
from prismclaw.schema.content.content_types import ToolResult  # noqa: E402


def test_fmt_for_path_covers_supported_suffixes():
    assert _fmt_for_path("a.docx") == "docx"
    assert _fmt_for_path("a.PDF") == "pdf"
    assert _fmt_for_path("a.md") == "markdown"
    assert _fmt_for_path("a.xlsx") == "xlsx"
    assert _fmt_for_path("a.pptx") == "pptx"
    assert _fmt_for_path("a.txt") is None


def test_fmt_for_path_agrees_with_single_source():
    """两处格式表已收敛为一处：路径解析结果必须与 DOC_FORMATS 一致。"""
    for ext, fmt in DOC_FORMATS.items():
        assert _fmt_for_path(f"x{ext}") == fmt


_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
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
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.re'
            'lationships+xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlform'
            'ats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="'
            + _REL_TYPE
            + "/officeDocument"
            + '" Target="word/document.xml"/>'
            "</Relationships>",
        )
        zf.writestr("word/document.xml", _DOC_TEXT_XML)
        zf.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )
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
    """read(.docx) 走文档分支：页头标注页数，正文含提取文本。"""
    (tmp_path / "doc.docx").write_bytes(_build_docx_with_text())
    result = _run_read(_make_read_tool(), {"file_path": "doc.docx"}, _make_read_ctx(tmp_path))
    assert not result.is_error
    text = _text_of(result)
    assert text.startswith("[read: doc.docx — 共 1 页，当前 p1（tier=text）]")
    assert "标题文本" in text


def test_docx_offset_gt_one_is_rejected(tmp_path):
    """docx 无页概念：offset>1 明确报错，不假装分页。"""
    (tmp_path / "doc.docx").write_bytes(_build_docx_with_text())
    result = _run_read(
        _make_read_tool(), {"file_path": "doc.docx", "offset": 5}, _make_read_ctx(tmp_path)
    )
    assert result.is_error
    assert "无页概念" in _text_of(result)


def test_docx_vision_tier_is_rejected(tmp_path):
    """docx 无页可渲染：tier=vision 明确报错。"""
    (tmp_path / "doc.docx").write_bytes(_build_docx_with_text())
    result = _run_read(
        _make_read_tool(), {"file_path": "doc.docx", "tier": "vision"}, _make_read_ctx(tmp_path)
    )
    assert result.is_error
    assert "vision" in _text_of(result)


def test_invalid_after_locator_returns_error(tmp_path):
    """非法 after 游标返回错误而非抛栈。"""
    (tmp_path / "doc.docx").write_bytes(_build_docx_with_text())
    result = _run_read(
        _make_read_tool(), {"file_path": "doc.docx", "after": "p1/b1"}, _make_read_ctx(tmp_path)
    )
    assert result.is_error
    assert "定位符格式错误" in _text_of(result)


def test_second_read_hits_cache(tmp_path, monkeypatch):
    """同一文档二次读取不再调用解析器（命中会话缓存）。"""
    import prismclaw.engine.toolkit.file.read_office as mod

    (tmp_path / "doc.docx").write_bytes(_build_docx_with_text())
    calls = {"n": 0}
    original = mod.parse_page

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(mod, "parse_page", counting)

    tool, ctx = _make_read_tool(), _make_read_ctx(tmp_path)
    _run_read(tool, {"file_path": "doc.docx"}, ctx)
    first = calls["n"]
    _run_read(tool, {"file_path": "doc.docx"}, ctx)
    assert first >= 1
    assert calls["n"] == first, "第二次读取不应重新解析"


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
    """read(.doc/.ppt) 给出可行动的转换提示，而非 UnicodeDecodeError。

    .xls 不在其中：旧式 xls 表格可由 xlrd 直读，走文档分支。
    """
    for ext in (".doc", ".ppt"):
        result = _run_read(_make_read_tool(), {"file_path": f"old{ext}"}, _make_read_ctx(tmp_path))
        assert result.is_error, ext
        assert "旧式二进制 Office 格式" in _text_of(result), ext


def test_read_text_path_unchanged_for_py(tmp_path):
    """纯文本路径行为不变：正常读取 + 分页元数据。"""
    (tmp_path / "code.py").write_text("def main():\n    return 42\n", encoding="utf-8")
    result = _run_read(_make_read_tool(), {"file_path": "code.py"}, _make_read_ctx(tmp_path))
    assert not result.is_error
    text = _text_of(result)
    assert "def main():" in text
    assert (result.metadata or {}).get("total_lines") == 2


def test_read_dispatches_md_to_document_branch(tmp_path):
    """read(.md) 走文档分支：页头标注页数，正文含原文。"""
    (tmp_path / "notes.md").write_text(
        "# 标题\n\n正文段落，含 [图](img.png) 引用。\n", encoding="utf-8"
    )
    result = _run_read(_make_read_tool(), {"file_path": "notes.md"}, _make_read_ctx(tmp_path))
    assert not result.is_error
    text = _text_of(result)
    assert text.startswith("[read: notes.md — 共 1 页，当前 p1（tier=text）]")
    assert "正文段落" in text


def test_vision_cache_is_reused_for_unreadable_page(tmp_path):
    """text 档遇到无文本层页时，复用已存在的 vision 缓存而非返回 unsupported。"""
    from prismclaw.engine.toolkit.office.document_reader import (
        Block,
        cache_dir,
        ensure_cache,
        save_page,
    )

    (tmp_path / "doc.md").write_text("第一段\n", encoding="utf-8")
    ctx = _make_read_ctx(tmp_path)
    _run_read(_make_read_tool(), {"file_path": "doc.md"}, ctx)

    raw = (tmp_path / "doc.md").read_bytes()
    target = cache_dir(tmp_path, raw, "doc.md")
    ensure_cache(target, fmt="markdown", pages=1)
    save_page(target, 1, "text", [Block(kind="unsupported", page=1, index=1, text="无文本层")])
    save_page(
        target, 1, "vision", [Block(kind="text", page=1, index=1, text="【视觉结果】识别出的内容")]
    )

    result = _run_read(_make_read_tool(), {"file_path": "doc.md"}, ctx)
    assert not result.is_error
    assert "视觉结果" in _text_of(result)


def test_scanned_attachment_keeps_a_hint(tmp_path):
    """扫描件附件不返回空块：保留 unsupported 提示，模型据此可改用 vision。"""
    from prismclaw.engine.execution.request.attachments import _extract_file_blocks
    from prismclaw.schema.agent.data_types import FileBlock

    (tmp_path / "scan.pdf").write_bytes(_minimal_blank_pdf())
    fb = FileBlock(type="file", path="scan.pdf", name="scan.pdf", mime_type="application/pdf")
    blocks = _extract_file_blocks(fb, tmp_path)

    assert blocks, "扫描件附件不应返回空列表"
    assert any("扫描件" in getattr(b, "text", "") for b in blocks)


def _minimal_blank_pdf() -> bytes:
    """构造一页无文本层的 PDF（空内容流）。"""
    import io

    content = ""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> /Contents 4 0 R >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    return out.getvalue()
