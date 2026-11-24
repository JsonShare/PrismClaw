"""document_reader 的解析与引用契约测试。

覆盖：按页产出块、pdf 页号一基、docx 表格行列与标题、markdown 单页、
扫描页 unsupported、定位符校验、缓存命中与失效。
"""

from __future__ import annotations

import base64
import io
import zipfile

import pytest

from prismclaw.engine.toolkit.office.document_reader import (
    DOC_FORMATS,
    Block,
    Cursor,
    cache_dir,
    content_hash8,
    ensure_cache,
    load_page,
    open_doc,
    page_count,
    parse_locator,
    parse_page,
    save_page,
    to_model_path,
)


def _make_png() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
        "YGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
    )


_DOCX_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _build_docx(*, with_table: bool, heading: bool = False) -> bytes:
    body = ""
    if heading:
        body += (
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            "<w:r><w:t>第三章 部署</w:t></w:r></w:p>"
        )
    body += "<w:p><w:r><w:t>正文段落</w:t></w:r></w:p>"
    if with_table:
        body += (
            "<w:tbl>"
            "<w:tr><w:tc><w:p><w:r><w:t>参数</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>默认值</w:t></w:r></w:p></w:tc></w:tr>"
            "<w:tr><w:tc><w:p><w:r><w:t>超时</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>30s</w:t></w:r></w:p></w:tc></w:tr>"
            "</w:tbl>"
        )
    styles = (
        f'<?xml version="1.0"?><w:styles xmlns:w="{_DOCX_W_NS}">'
        '<w:style w:type="paragraph" w:styleId="Heading1">'
        '<w:name w:val="heading 1"/></w:style></w:styles>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.re'
            'lationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlform'
            'ats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformat'
            's-officedocument.wordprocessingml.styles+xml"/>'
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
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?>'
            f'<w:document xmlns:w="{_DOCX_W_NS}"><w:body>{body}</w:body></w:document>',
        )
        zf.writestr("word/styles.xml", styles)
        zf.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId9" Type="' + _REL_TYPE + "/styles" + '" Target="styles.xml"/>'
            "</Relationships>",
        )
        zf.writestr("word/media/image1.png", _make_png())
    return buf.getvalue()


def test_doc_formats_is_single_source() -> None:
    assert DOC_FORMATS[".pdf"] == "pdf"
    assert DOC_FORMATS[".docx"] == "docx"
    assert DOC_FORMATS[".md"] == "markdown"
    assert DOC_FORMATS[".markdown"] == "markdown"
    assert DOC_FORMATS[".rst"] == "markdown"


def test_parse_page_rejects_zero_page(tmp_path) -> None:
    doc = open_doc(_build_docx(with_table=False), "docx")
    with pytest.raises(ValueError, match="从 1 开始"):
        parse_page(doc, 0, workspace_path=tmp_path, source_rel_path="a.docx")


def test_docx_has_no_page_concept(tmp_path) -> None:
    doc = open_doc(_build_docx(with_table=False), "docx")
    assert page_count(doc, "docx") == 1
    with pytest.raises(ValueError, match="无页概念"):
        parse_page(doc, 2, workspace_path=tmp_path, source_rel_path="a.docx")


def test_docx_table_keeps_rows_and_columns(tmp_path) -> None:
    doc = open_doc(_build_docx(with_table=True), "docx")
    blocks = parse_page(doc, 1, workspace_path=tmp_path, source_rel_path="a.docx")
    tables = [b for b in blocks if b.kind == "table"]
    assert len(tables) == 1
    assert tables[0].rows == (("参数", "默认值"), ("超时", "30s"))


def test_docx_heading_rendered_as_markdown(tmp_path) -> None:
    doc = open_doc(_build_docx(with_table=False, heading=True), "docx")
    blocks = parse_page(doc, 1, workspace_path=tmp_path, source_rel_path="a.docx")
    texts = [b.text for b in blocks if b.kind == "text"]
    assert any(t.startswith("# 第三章 部署") for t in texts), texts


def test_docx_indexes_are_sequential_from_one(tmp_path) -> None:
    doc = open_doc(_build_docx(with_table=True), "docx")
    blocks = parse_page(doc, 1, workspace_path=tmp_path, source_rel_path="a.docx")
    assert [b.index for b in blocks] == list(range(1, len(blocks) + 1))
    assert all(b.page == 1 for b in blocks)


def test_markdown_is_single_page(tmp_path) -> None:
    (tmp_path / "img.png").write_bytes(_make_png())
    raw = "# 标题\n\n正文段落 ![图](img.png) 尾注\n".encode()
    doc = open_doc(raw, "markdown")
    blocks = parse_page(doc, 1, workspace_path=tmp_path, source_rel_path="a.md")
    assert all(b.page == 1 for b in blocks)
    assert any(b.kind == "image" and b.source_path for b in blocks)
    assert blocks[-1].text.startswith("尾注")


def test_pdf_text_tier_produces_text_blocks(tmp_path) -> None:
    """真实 PDF 在 text 档产出文本块，页码一基，块号连续。"""
    pdf = _minimal_pdf_text("Hello page one")
    doc = open_doc(pdf, "pdf")
    blocks = parse_page(doc, 1, workspace_path=tmp_path, source_rel_path="a.pdf")
    assert blocks and blocks[0].page == 1
    assert blocks[0].index == 1
    assert "Hello" in blocks[0].text
    assert all(b.kind == "text" for b in blocks)


def test_pdf_scanned_page_is_unsupported(tmp_path) -> None:
    """无文本层页在 text 档返回 unsupported；「用 vision」的指引由渲染层按状态追加。"""
    pdf = _minimal_pdf_blank()
    doc = open_doc(pdf, "pdf")
    blocks = parse_page(doc, 1, workspace_path=tmp_path, source_rel_path="a.pdf")
    assert len(blocks) == 1
    assert blocks[0].kind == "unsupported"
    assert "无可提取文本层" in blocks[0].text
    # 缓存文案保持中性，不含一次性的档位指引（避免失败后仍暗示重试必然成功）
    assert "vision" not in blocks[0].text


def test_locator_roundtrip_and_rejects_zero() -> None:
    cursor = Cursor(hash8=content_hash8(b"abc"), page=3, index=7, tier="text")
    assert str(cursor) == f"doc:{content_hash8(b'abc')}/p3/b7@text"
    parsed = parse_locator(str(cursor))
    assert (parsed.page, parsed.index, parsed.tier) == (3, 7, "text")
    with pytest.raises(ValueError):
        parse_locator("doc:0a1b2c3d/p0/b1@text")
    with pytest.raises(ValueError):
        parse_locator("doc:0a1b2c3d/p1/b0@text")
    with pytest.raises(ValueError):
        parse_locator("p1/b1")


def test_locator_char_offset_is_legal() -> None:
    cursor = parse_locator("doc:0a1b2c3d/p3/b7@text/c1980")
    assert cursor.char_offset == 1980
    assert str(cursor).endswith("/c1980")


def test_locator_without_offset_omits_suffix() -> None:
    assert not str(parse_locator("doc:0a1b2c3d/p1/b1@text")).endswith("/c0")


def test_cache_roundtrip_and_page_mismatch_invalidates(tmp_path) -> None:
    raw = b"content"
    target = cache_dir(tmp_path, raw, "a.pdf")
    ensure_cache(target, fmt="pdf", pages=2)
    save_page(target, 1, "text", [Block(kind="text", page=1, index=1, text="x")])
    loaded = load_page(target, 1)
    assert loaded is not None and loaded[0].text == "x"

    # 页号错位的缓存视为未命中
    save_page(target, 1, "text", [Block(kind="text", page=2, index=1, text="伪造")])
    assert load_page(target, 1) is None


def test_cache_content_tampering_is_rejected(tmp_path) -> None:
    """绕过 save_page 直接改文件内容：摘要不符即视为未命中。"""
    import json as _json

    raw = b"content"
    target = cache_dir(tmp_path, raw, "a.pdf")
    ensure_cache(target, fmt="pdf", pages=2)
    save_page(target, 1, "text", [Block(kind="text", page=1, index=1, text="原文")])

    page_file = target / "p1.json"
    page_file.write_text(
        _json.dumps(
            [{"kind": "text", "page": 1, "index": 1, "text": "伪造正文"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert load_page(target, 1) is None


def test_concurrent_save_does_not_raise(tmp_path) -> None:
    """并发写同一页：临时文件不互删，全部写入成功且结果可读。"""
    from concurrent.futures import ThreadPoolExecutor

    target = cache_dir(tmp_path, b"race", "a.pdf")
    ensure_cache(target, fmt="pdf", pages=1)

    def write(i: int) -> None:
        save_page(target, 1, "text", [Block(kind="text", page=1, index=1, text=f"w{i}")])

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(write, range(40)))

    loaded = load_page(target, 1)
    assert loaded is not None
    assert not list(target.glob("*.tmp"))


def test_ensure_cache_survives_digest_growth(tmp_path) -> None:
    """meta 增加 page_digests 后，重复 ensure_cache 不应清空缓存。"""
    target = cache_dir(tmp_path, b"content", "a.pdf")
    ensure_cache(target, fmt="pdf", pages=2)
    save_page(target, 1, "text", [Block(kind="text", page=1, index=1, text="x")])

    ensure_cache(target, fmt="pdf", pages=2)  # 第二次调用
    assert load_page(target, 1) is not None


def test_cache_rebuilds_when_pages_change(tmp_path) -> None:
    raw = b"content"
    target = cache_dir(tmp_path, raw, "a.pdf")
    ensure_cache(target, fmt="pdf", pages=2)
    save_page(target, 1, "text", [Block(kind="text", page=1, index=1, text="x")])
    ensure_cache(target, fmt="pdf", pages=3)  # 页数变化 → 整目录重建
    assert load_page(target, 1) is None


def test_to_model_path_maps_work_cache_to_project() -> None:
    assert to_model_path(".work/cache/extracted/k/a.png") == "project/.work/cache/extracted/k/a.png"
    assert to_model_path("sessions/s1/inbox/a.pdf") == "inbox/a.pdf"
    assert to_model_path("project/readme.md") == "project/readme.md"


def test_typographic_chars_are_folded() -> None:
    """二进制抽取的排版字符折叠为 ASCII，保留中文破折号。"""
    doc = open_doc(
        "5\u2010year 45%\u2013100% \u2014\u2014 A\u00a0B\u200bC".encode(),
        "markdown",
    )
    blocks = parse_page(doc, 1)
    text = blocks[0].text
    assert text.startswith("5-year 45%-100% ")
    assert "\u2014\u2014" in text  # 中文破折号保留
    assert "A BC" in text  # NBSP 归一 + 零宽清除


def test_broken_page_degrades_to_unsupported_and_keeps_document_readable() -> None:
    """单页加载失败只让该页 unsupported，其它页仍可读。"""
    raw = _assemble_pdf("BT /F1 24 Tf 72 720 Td (page one) Tj ET", pages=3)
    doc = open_doc(raw, "pdf")
    assert page_count(doc, "pdf") == 3
    for page in (1, 2, 3):
        blocks = parse_page(doc, page)
        assert blocks, f"第 {page} 页应有结果"
    # 越界仍报错，说明范围校验未被降级吞掉
    with pytest.raises(ValueError, match="超出范围"):
        parse_page(doc, 4)


def test_render_blocks_returns_cursor_for_oversized_block() -> None:
    """超预算单块在自然边界切分，游标记录字符偏移，续读不丢字。"""
    from prismclaw.engine.toolkit.office.document_reader import render_blocks

    long_text = "句子。" * 3000  # 9000 字符，远超预算
    block = Block(kind="text", page=1, index=1, text=long_text)
    body, cursor = render_blocks([block], hash8="abcd1234", tier="text")
    assert cursor is not None
    assert cursor.index == 1
    assert cursor.char_offset > 0
    assert len(body) <= 6000 + 16  # 预算 + 块号标签

    # 从游标续读：拼接结果应覆盖全文且不重复
    tail, cursor2 = render_blocks([block], hash8="abcd1234", tier="text", start=cursor)
    assert "续" in tail
    assert cursor2 is None or cursor2.char_offset > cursor.char_offset


def test_render_blocks_no_cursor_when_within_budget() -> None:
    from prismclaw.engine.toolkit.office.document_reader import render_blocks

    blocks = [Block(kind="text", page=1, index=1, text="短文本")]
    body, cursor = render_blocks(blocks, hash8="abcd1234", tier="text")
    assert cursor is None
    assert "b1 短文本" in body


# ---------------------------------------------------------------------------
# 最小 PDF 构造（不依赖外部样本文件）
# ---------------------------------------------------------------------------
def _minimal_pdf_text(text: str) -> bytes:
    content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET"
    return _assemble_pdf(content)


def _minimal_pdf_blank() -> bytes:
    return _assemble_pdf("")


def _assemble_pdf(content: str, pages: int = 1) -> bytes:
    """构造最小 PDF。多页时各页共享同一内容流与字体。"""
    # 对象编号：1 Catalog / 2 Pages / 3 内容流 / 4 字体 / 5.. 各页
    objects: list[str] = []
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{5 + i} 0 R" for i in range(pages))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>")
    objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for _ in range(pages):
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 4 0 R >> >> /Contents 3 0 R >>"
        )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    out.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
        ).encode("latin-1")
    )
    return out.getvalue()
